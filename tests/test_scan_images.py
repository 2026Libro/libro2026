"""로봇 사진 업로드 → 백엔드가 AI OCR 호출 → 책마다 관측 → 매칭·흐름 검사 → 자동 분석 완료."""
import struct
import zlib
from contextlib import nullcontext

import pytest

from app.core.config import get_settings
from app.core.enums import DeviceType
from app.db.session import get_session_factory
from app.main import app
from app.models import Robot
from app.services import ai_ocr
from app.services.device import issue_api_key
from tests.conftest import ADMIN_HEADERS
from tests.test_shelf_order import CALLS


def png(width=1280, height=720) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IEND", b""))


# 시집(811.16) 사이에 경제 책(330.9) — AI는 글자 조각과 박스만 준다
OCR = {"books": [
    {"order": 1, "bbox": [100, 50, 80, 600], "detection_confidence": 0.97,
     "fragments": [{"text": "000", "confidence": 0.99}, {"text": "811.16", "confidence": 0.95},
                   {"text": "김54ㄱ", "confidence": 0.9}, {"text": "c.2", "confidence": 0.8}]},
    {"order": 2, "bbox": [190, 60, 70, 590], "fragments": [{"text": "330.9", "confidence": 0.93}, "박12ㄱ"]},
    {"order": 3, "bbox": [270, 40, 90, 610], "fragments": [{"text": "811.16", "confidence": 0.94},
                                                           {"text": "박1ㄱ", "confidence": 0.88}]},
]}


@pytest.fixture
def env(client, db, tmp_path, monkeypatch):
    db.add_all([Robot(id="R980"), Robot(id="R981")])
    db.commit()
    client.post("/shelves", json={"id": "S-01", "slot_count": 2}, headers=ADMIN_HEADERS)
    book_ids = {}
    for copy_id, call in CALLS.items():
        base = call.removesuffix(" c.2")
        if base not in book_ids:
            book_ids[base] = client.post("/books", json={"title": f"책 {base}", "call_number": base},
                                         headers=ADMIN_HEADERS).json()["id"]
        client.post(f"/books/{book_ids[base]}/copies", headers=ADMIN_HEADERS,
                    json={"id": copy_id, "call_number": call, "room": "고양캠퍼스 대출실"})
    session_id = client.post("/scan-sessions", json={"robot_id": "R980", "shelf_id": "S-01"},
                             headers=ADMIN_HEADERS).json()["id"]
    settings = app.dependency_overrides[get_settings]().model_copy(
        update={"image_root": str(tmp_path), "ai_ocr_url": "http://ai.test/ocr"})
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_factory] = lambda: (lambda: nullcontext(db))
    calls = []

    def fake_ocr(_settings, image, path):
        calls.append((image.id, image.slot_id, path.read_bytes()[:4]))
        return OCR

    monkeypatch.setattr(ai_ocr, "request_ocr", fake_ocr)
    return {"session_id": session_id, "settings": settings, "calls": calls, "tmp": tmp_path,
            "robot": {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R980")},
            "other_robot": {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R981")},
            "ai": {"X-API-Key": issue_api_key(db, DeviceType.AI, "ai-ocr")}}


def upload(client, env, slot="S-01-01", minute=0, expect=202, headers=None, body=None, ctype="image/png", **params):
    res = client.post(f"/scan-sessions/{env['session_id']}/images",
                      params={"slot_id": slot, "captured_at": f"2026-11-23T16:{minute:02d}:00+09:00", **params},
                      content=png() if body is None else body,
                      headers={"Content-Type": ctype, **(headers or env["robot"])})
    assert res.status_code == expect, res.text
    return res.json()


def finish(client, env):
    res = client.post(f"/scan-sessions/{env['session_id']}/finish", json={"status": "COMPLETED"}, headers=env["robot"])
    assert res.status_code == 200, res.text


def books(client, env, slot="S-01-01"):
    return client.get(f"/scan-sessions/{env['session_id']}/slots/{slot}/order-check").json()["images"][0]["books"]


def test_upload_calls_ai_and_creates_observations(client, env):
    image = upload(client, env)
    assert image["id"].startswith("IMG-") and (image["image_width"], image["image_height"]) == (1280, 720)
    assert env["calls"] == [(image["id"], "S-01-01", b"\x89PNG")]                 # 저장한 사진을 AI에 보냄

    stored = client.get(f"/scan-sessions/{env['session_id']}/images").json()
    assert [(i["ocr_status"], i["book_count"]) for i in stored] == [("DONE", 3)]
    file = client.get(stored[0]["image_url"])
    assert file.status_code == 200 and file.content[:4] == b"\x89PNG"

    obs = client.get("/observations", params={"scan_session_id": env["session_id"], "limit": 10}).json()["items"]
    first = min(obs, key=lambda o: o["order"])
    assert (first["match_method"], first["reported_book_copy_id"]) == ("FRAGMENTS", "EM9800002")   # 김54ㄱ c.2
    assert (first["bbox"], first["image_ref"], first["detection_confidence"]) == (
        [100, 50, 80, 600], stored[0]["image_ref"], 0.97)
    assert first["text_fragments"] == ["000", "811.16", "김54ㄱ", "c.2"]
    assert first["ocr_confidence"] == round((0.99 + 0.95 + 0.9 + 0.8) / 4, 3)
    assert [b["status"] for b in books(client, env)] == ["IN_ORDER", "OUT_OF_PLACE", "IN_ORDER"]


def test_finish_after_ocr_completes_analysis_automatically(client, env):
    upload(client, env)
    finish(client, env)                         # 사진 OCR이 이미 끝남 → 바로 분석 완료
    page = client.get("/verifications", params={"scan_session_id": env["session_id"], "limit": 100}).json()
    assert sorted(v["sequence_status"] for v in page["items"] if v["observation_id"]) == [
        "IN_ORDER", "IN_ORDER", "OUT_OF_PLACE"]
    alerts = client.get("/alerts", params={"alert_type": "OUT_OF_PLACE"}, headers=ADMIN_HEADERS).json()
    assert [a["book_copy_id"] for a in alerts["items"]] == ["EM9800007"]
    again = client.post(f"/scan-sessions/{env['session_id']}/analysis-complete", headers=env["ai"])
    assert again.status_code == 409                 # 이미 처리됨


def test_ai_pushes_result_when_backend_does_not_call_ai(client, env):
    settings = env["settings"].model_copy(update={"ai_ocr_url": ""})
    app.dependency_overrides[get_settings] = lambda: settings
    image = upload(client, env)
    assert (image["ocr_status"], env["calls"]) == ("PENDING", [])
    finish(client, env)                             # 아직 OCR 전 → 분석 완료하지 않음
    assert client.get("/verifications", params={"scan_session_id": env["session_id"]}).json()["total"] == 0

    no_key = client.post(f"/scan-images/{image['id']}/ocr-result", json=OCR)
    assert no_key.status_code == 401
    res = client.post(f"/scan-images/{image['id']}/ocr-result", json=OCR, headers=env["ai"])
    assert res.status_code == 200 and res.json()["ocr_status"] == "DONE", res.text
    page = client.get("/verifications", params={"scan_session_id": env["session_id"], "limit": 100}).json()
    assert "OUT_OF_PLACE" in {v["sequence_status"] for v in page["items"]}      # 마지막 사진 OCR 때 분석 완료
    assert client.post(f"/scan-images/{image['id']}/ocr-result", json=OCR, headers=env["ai"]).status_code == 409


def test_ai_failure_is_recorded_and_admin_can_retry(client, env, monkeypatch):
    def broken(*_):
        raise ai_ocr.OcrCallError("AI OCR 서버 호출 실패: 연결 거부")
    monkeypatch.setattr(ai_ocr, "request_ocr", broken)
    image = upload(client, env)
    failed = client.get(f"/scan-images/{image['id']}").json()
    assert (failed["ocr_status"], failed["ocr_error"]) == ("FAILED", "AI OCR 서버 호출 실패: 연결 거부")
    finish(client, env)
    assert client.get("/verifications", params={"scan_session_id": env["session_id"]}).json()["total"] == 0

    monkeypatch.setattr(ai_ocr, "request_ocr", lambda *_: OCR)
    assert client.post(f"/scan-images/{image['id']}/retry-ocr").status_code == 401
    res = client.post(f"/scan-images/{image['id']}/retry-ocr", headers=ADMIN_HEADERS)
    assert res.status_code == 202, res.text
    assert client.get(f"/scan-images/{image['id']}").json()["ocr_status"] == "DONE"
    assert client.get("/verifications", params={"scan_session_id": env["session_id"]}).json()["total"] == 3


def test_bad_ai_response_marks_failed(client, env, monkeypatch):
    monkeypatch.setattr(ai_ocr, "request_ocr", lambda *_: {"books": [{"bbox": [0, 0, -5, 10]}]})
    image = upload(client, env)
    assert client.get(f"/scan-images/{image['id']}").json()["ocr_status"] == "FAILED"


def test_order_taken_from_bbox_when_missing(client, env, monkeypatch):
    shuffled = {"books": [{**b, "order": None} for b in reversed(OCR["books"])]}
    monkeypatch.setattr(ai_ocr, "request_ocr", lambda *_: shuffled)
    upload(client, env)
    assert [b["bbox"][0] for b in books(client, env)] == [100, 190, 270]


def test_upload_validation(client, env):
    upload(client, env, ctype="text/plain", expect=415)
    upload(client, env, body=b"not an image", expect=422)
    upload(client, env, slot="S-02-01", expect=422)                       # 다른 서가 칸
    upload(client, env, headers=env["other_robot"], expect=403)           # 세션을 맡은 로봇이 아님
    upload(client, env, headers=env["ai"], expect=403)                    # AI 키로는 올릴 수 없음
    first = upload(client, env, image_id="IMG-R980-0001")
    again = upload(client, env, image_id="IMG-R980-0001", expect=200)     # 재전송: 한 번만 저장
    assert again["id"] == first["id"] and len(env["calls"]) == 1
    finish(client, env)
    upload(client, env, expect=409)                                       # 종료된 세션


def test_request_ocr_sends_photo_and_reads_json(tmp_path, monkeypatch):
    import httpx
    from types import SimpleNamespace
    sent = {}

    def fake_post(url, content, headers, params, timeout):
        sent.update(url=url, content=content, headers=headers, params=params, timeout=timeout)
        return httpx.Response(200, json=OCR, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    path = tmp_path / "a.png"
    path.write_bytes(png())
    settings = get_settings().model_copy(update={"ai_ocr_url": "http://ai.test/ocr", "ai_ocr_api_key": "k1"})
    image = SimpleNamespace(id="IMG-1", scan_session_id="SCAN-1", slot_id="S-01-01", content_type="image/png")
    assert ai_ocr.request_ocr(settings, image, path) == OCR
    assert sent["headers"] == {"Content-Type": "image/png", "X-API-Key": "k1"}
    assert sent["params"] == {"image_id": "IMG-1", "scan_session_id": "SCAN-1", "slot_id": "S-01-01"}
    assert sent["content"][:4] == b"\x89PNG" and sent["timeout"] == 120.0

    monkeypatch.setattr(httpx, "post", lambda url, **_: httpx.Response(500, text="boom", request=httpx.Request("POST", url)))
    with pytest.raises(ai_ocr.OcrCallError, match="응답 500"):
        ai_ocr.request_ocr(settings, image, path)


def test_robot_opens_its_own_session(client, env):
    res = client.post("/scan-sessions", json={"robot_id": "R980", "shelf_id": "S-01"}, headers=env["robot"])
    assert res.status_code == 201, res.text
    sid = res.json()["id"]
    up = client.post(f"/scan-sessions/{sid}/images", params={"slot_id": "S-01-01", "captured_at": "2026-11-24T10:00:00+09:00"},
                     content=png(), headers={"Content-Type": "image/png", **env["robot"]})
    assert up.status_code == 202, up.text
    other = client.post("/scan-sessions", json={"robot_id": "R981", "shelf_id": "S-01"}, headers=env["robot"])
    assert other.status_code == 403                               # 다른 로봇 세션은 못 엶
    assert client.post("/scan-sessions", json={"robot_id": "R980", "shelf_id": "S-01"},
                       headers=env["ai"]).status_code == 403      # AI 키로는 못 엶
    assert client.post("/scan-sessions", json={"robot_id": "R980", "shelf_id": "S-01"}).status_code == 401

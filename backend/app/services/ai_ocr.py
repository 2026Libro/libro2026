"""AI OCR 서버 호출.

[가정] 요청: POST {AI_OCR_URL}  (본문 = 사진 파일 그대로, Content-Type: image/jpeg 또는 image/png)
       쿼리: image_id, scan_session_id, slot_id   헤더: X-API-Key (AI_OCR_API_KEY가 있으면)
       응답: OcrResult JSON — {"books": [{"order": 1, "bbox": [x, y, w, h], "detection_confidence": 0.97,
                                            "fragments": [{"text": "2013한국인터넷백서", "confidence": 1.0}, …]}]}
AI 담당자와 형식을 정하면 이 파일만 고친다.
"""
from pathlib import Path

import httpx

from app.core.config import Settings
from app.models import ScanImage


class OcrCallError(Exception):
    """AI OCR 서버 호출 실패 (연결·시간 초과·HTTP 오류·JSON 아님)."""


def request_ocr(settings: Settings, image: ScanImage, path: Path) -> dict:
    headers = {"Content-Type": image.content_type}
    if settings.ai_ocr_api_key:
        headers["X-API-Key"] = settings.ai_ocr_api_key
    params = {"image_id": image.id, "scan_session_id": image.scan_session_id, "slot_id": image.slot_id}
    try:
        response = httpx.post(settings.ai_ocr_url, content=path.read_bytes(), headers=headers, params=params,
                              timeout=settings.ai_ocr_timeout_seconds)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise OcrCallError(f"AI OCR 서버 응답 {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise OcrCallError(f"AI OCR 서버 호출 실패: {exc}") from exc

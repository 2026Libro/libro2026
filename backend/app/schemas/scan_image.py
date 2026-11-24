"""로봇 사진 업로드와 AI OCR 결과 형식.

흐름: 로봇이 사진을 백엔드에 올림 → 백엔드가 AI OCR에 보냄 → AI가 책별 글자 조각·박스를 돌려줌
     → 백엔드가 책마다 관측을 만들어 매칭·흐름 검사.
[가정] AI OCR 응답 형식(OcrResult)은 AI 담당자와 협의 전이다. 지금 AI 출력(책 그룹별 글자 조각 + 신뢰도)에
박스 좌표만 더한 형태로 정했다.
"""
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.core.enums import OcrStatus
from app.schemas.common import UtcDatetime


class ScanImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scan_session_id: str
    slot_id: str
    image_ref: str
    image_width: int | None
    image_height: int | None
    captured_at: UtcDatetime
    received_at: UtcDatetime
    ocr_status: OcrStatus
    ocr_error: str | None
    book_count: int | None = Field(description="OCR이 찾은 책 수 (DONE일 때)")
    ocr_completed_at: UtcDatetime | None

    @computed_field
    @property
    def image_url(self) -> str:
        """사진 파일 경로. 서버 주소 뒤에 붙여 <img src>로 쓴다."""
        return f"/scan-images/{self.id}/file"


class OcrFragment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="0.0~1.0 (93.8% → 0.938)")


class OcrBook(BaseModel):
    """책 1권(책등 1개)."""

    model_config = ConfigDict(extra="ignore")

    order: int | None = Field(default=None, ge=1, description="사진 안 왼쪽부터 순번. 없으면 bbox x 좌표로 정한다")
    bbox: list[int] | None = Field(default=None, min_length=4, max_length=4,
                                   description="원본 픽셀 [x, y, width, height], 왼쪽 위 원점")
    detection_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    fragments: list[OcrFragment | str] = Field(default_factory=list, description="읽은 글자 조각 (순서 무관)")

    @model_validator(mode="after")
    def _check_bbox(self) -> "OcrBook":
        if self.bbox is not None:
            x, y, w, h = self.bbox
            if x < 0 or y < 0 or w <= 0 or h <= 0:
                raise ValueError("bbox는 [x, y, width, height]이며 x·y는 0 이상, width·height는 1 이상이어야 합니다.")
        return self

    def texts(self) -> list[tuple[str, float | None]]:
        out = []
        for f in self.fragments:
            text, conf = (f, None) if isinstance(f, str) else (f.text, f.confidence)
            if text.strip():
                out.append((text.strip()[:200], conf))
        # 관측은 조각 30개까지 받으므로 신뢰도 높은 순으로 자른다
        if len(out) > 30:
            out = sorted(out, key=lambda t: -(t[1] or 0))[:30]
        return out


class OcrResult(BaseModel):
    """AI OCR 응답 (또는 AI가 POST /scan-images/{id}/ocr-result로 보내는 본문)."""

    model_config = ConfigDict(extra="ignore")

    books: list[OcrBook] = Field(max_length=200)

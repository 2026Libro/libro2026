"""AI → 백엔드 인식 결과 데이터 규격 (기획서 8장 초안 기준).

[가정] 아래 항목은 AI 담당자와 협의 전이다.
- 필드 이름과 구성 전체
- image_ref 형식 (현재는 상대경로 문자열로 받음)
- 신뢰도는 0.0~1.0 범위로 받음. 임계값은 여기서 정하지 않는다.
- 정의되지 않은 필드가 오면 거부한다 (오타를 조기에 발견하기 위함).
"""
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field, model_validator

from app.core.ids import BookCopyId, ObservationId, RobotId, ScanSessionId, ShelfId, SlotId, slot_belongs_to_shelf
from app.schemas.common import UtcDatetime


class Confidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detection: float = Field(ge=0.0, le=1.0)
    ocr: float = Field(ge=0.0, le=1.0)
    identification: float = Field(default=0.0, ge=0.0, le=1.0,
                                  description="AI가 등록번호를 직접 고른 경우의 신뢰도. 글자만 보내면 생략 (백엔드 매칭 점수 사용)")


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_copy_id: BookCopyId
    score: float = Field(ge=0.0, le=1.0)


class ObservationCreate(BaseModel):
    """POST /observations 요청 본문.

    book_copy_id는 AI가 개별 복본을 확정하지 못하면 null이다.
    후보 점수가 높아도 백엔드가 이 스키마 단계에서 복본을 확정하지 않는다 (식별은 6단계 서비스 로직).
    """

    model_config = ConfigDict(extra="forbid")

    observation_id: ObservationId        # 중복 수신 방지 키
    scan_session_id: ScanSessionId
    robot_id: RobotId
    shelf_id: ShelfId
    slot_id: SlotId
    detected_text: str | None = Field(default=None, max_length=500)
    book_copy_id: BookCopyId | None = None
    call_number_text: str | None = Field(
        default=None, max_length=200,
        description="OCR로 읽은 청구기호 라벨 (줄바꿈 그대로 가능). book_copy_id·candidates가 없으면 백엔드가 이것으로 매칭")
    title_text: str | None = Field(default=None, max_length=500, description="OCR로 읽은 서명 (매칭 보조)")
    text_fragments: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=30,
        description="OCR이 책 1권에서 읽은 글자 조각들 (라벨 줄·제목 일부·띠지 숫자 등, 순서 무관). "
                    "book_copy_id·candidates가 없으면 백엔드가 이것으로 매칭 (call_number_text로 못 찾을 때도)")
    confidence: Confidence
    candidates: list[Candidate] = Field(default_factory=list, max_length=20)
    reobservation_of: ObservationId | None = None
    image_ref: str | None = Field(default=None, max_length=500,
                                  description="이미지 ID 겸 파일 경로. 같은 사진에서 나온 책은 같은 값")
    bbox: list[int] | None = Field(default=None, min_length=4, max_length=4,
                                   description="원본 이미지 픽셀 기준 책등 위치 [x, y, width, height]")
    order: int | None = Field(default=None, ge=1, description="같은 이미지 안에서 왼쪽부터 순번 (1부터)")
    image_width: int | None = Field(default=None, ge=1, description="원본 이미지 가로 픽셀")
    image_height: int | None = Field(default=None, ge=1, description="원본 이미지 세로 픽셀")
    captured_at: AwareDatetime           # 시간대(+09:00 등) 필수

    @model_validator(mode="after")
    def _check_consistency(self) -> "ObservationCreate":
        if not slot_belongs_to_shelf(self.slot_id, self.shelf_id):
            raise ValueError(f"slot_id '{self.slot_id}'는 shelf_id '{self.shelf_id}'에 속하지 않습니다.")

        ids = [c.book_copy_id for c in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidates에 같은 book_copy_id가 중복되어 있습니다.")

        if self.reobservation_of == self.observation_id:
            raise ValueError("reobservation_of는 자기 자신을 가리킬 수 없습니다.")

        if self.bbox is not None:
            x, y, w, h = self.bbox
            if x < 0 or y < 0 or w <= 0 or h <= 0:
                raise ValueError("bbox는 [x, y, width, height]이며 x·y는 0 이상, width·height는 1 이상이어야 합니다.")
            if self.image_width and x + w > self.image_width or self.image_height and y + h > self.image_height:
                raise ValueError("bbox가 이미지 크기(image_width·image_height)를 벗어납니다.")
        if (self.order is not None or self.bbox is not None) and not self.image_ref:
            raise ValueError("bbox·order는 이미지 안 위치이므로 image_ref(이미지 ID)가 함께 필요합니다.")
        return self


class CandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    book_copy_id: str
    score: float
    rank: int


class ObservationRead(BaseModel):
    """저장된 관측 결과. 식별·위치 판정 결과는 6단계의 /verifications에서 제공한다."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    scan_session_id: str
    robot_id: str
    shelf_id: str
    slot_id: str
    detected_text: str | None
    reported_book_copy_id: str | None
    match_method: str | None
    detection_confidence: float
    ocr_confidence: float
    identification_confidence: float
    candidates: list[CandidateRead]
    reobservation_of: str | None
    image_ref: str | None
    bbox: list[int] | None
    order: int | None
    image_width: int | None
    image_height: int | None
    captured_at: UtcDatetime
    received_at: UtcDatetime

    @computed_field
    @property
    def image_url(self) -> str | None:
        """이미지 조회 API 경로 (image_ref가 없으면 null). 서버 주소 뒤에 붙여 <img src>로 쓴다."""
        return f"/observations/{self.id}/image" if self.image_ref else None

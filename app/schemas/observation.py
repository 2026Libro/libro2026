"""AI → 백엔드 인식 결과 데이터 규격 (기획서 8장 초안 기준).

[가정] 아래 항목은 AI 담당자와 협의 전이다.
- 필드 이름과 구성 전체
- image_ref 형식 (현재는 상대경로 문자열로 받음)
- 신뢰도는 0.0~1.0 범위로 받음. 임계값은 여기서 정하지 않는다.
- 정의되지 않은 필드가 오면 거부한다 (오타를 조기에 발견하기 위함).
"""
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.core.ids import BookCopyId, ObservationId, RobotId, ScanSessionId, ShelfId, SlotId, slot_belongs_to_shelf
from app.schemas.common import UtcDatetime


class Confidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detection: float = Field(ge=0.0, le=1.0)
    ocr: float = Field(ge=0.0, le=1.0)
    identification: float = Field(ge=0.0, le=1.0)


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
    confidence: Confidence
    candidates: list[Candidate] = Field(default_factory=list, max_length=20)
    reobservation_of: ObservationId | None = None
    image_ref: str | None = Field(default=None, max_length=500)
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
    detection_confidence: float
    ocr_confidence: float
    identification_confidence: float
    candidates: list[CandidateRead]
    reobservation_of: str | None
    image_ref: str | None
    captured_at: UtcDatetime
    received_at: UtcDatetime

"""시스템 전체에서 쓰는 상태 코드.

의미가 다른 상태는 서로 다른 Enum으로 분리한다.
(예: REVIEW_REQUIRED는 위치 판정이 아니라 처리 흐름 상태이므로 WorkflowStatus에 둔다.)

StrEnum을 쓰므로 JSON/DB에는 "NORMAL" 같은 문자열 그대로 저장된다.
"""
from enum import StrEnum


class IdentificationStatus(StrEnum):
    """도서 식별 상태 (BE-005)."""

    IDENTIFIED = "IDENTIFIED"          # 개별 복본까지 확정
    CANDIDATE_ONLY = "CANDIDATE_ONLY"  # 후보는 있으나 복본 미확정
    UNCERTAIN = "UNCERTAIN"            # 신뢰도 부족
    UNREGISTERED = "UNREGISTERED"      # DB에 없는 도서로 보임
    FAILED = "FAILED"                  # 인식 실패


class LocationVerdict(StrEnum):
    """위치 판정 상태 (BE-006)."""

    NORMAL = "NORMAL"
    MISPLACED_SUSPECTED = "MISPLACED_SUSPECTED"
    MISPLACED_CONFIRMED = "MISPLACED_CONFIRMED"
    UNDETERMINED = "UNDETERMINED"


class RegisteredLocationSource(StrEnum):
    """등록 위치를 누가 정했나."""

    MANUAL = "MANUAL"          # 관리자가 직접 지정 (범위표 적용 시 덮어쓰지 않음)
    CALL_RANGE = "CALL_RANGE"  # 칸별 청구기호 범위표로 계산


class ShelfOrderStatus(StrEnum):
    """서가 청구기호 흐름 검사 결과 (책 1권 기준). 서가 전체를 칸 순서 → 사진 → 왼쪽부터 순번으로 이어 본다."""

    IN_ORDER = "IN_ORDER"            # 청구기호 흐름에 맞게 꽂혀 있음
    OUT_OF_ORDER = "OUT_OF_ORDER"    # 순서만 어긋남 (앞뒤 책과 같은 분류 근처, 가벼운 오류)
    OUT_OF_PLACE = "OUT_OF_PLACE"    # 엉뚱한 책 (앞뒤 책과 분류번호 앞 3자리가 다름)
    UNIDENTIFIED = "UNIDENTIFIED"    # 청구기호를 알 수 없어 검사에서 뺌 (식별 실패, 후보가 서로 다른 책)
    DUPLICATE = "DUPLICATE"          # 겹쳐 찍힌 사진에서 앞 사진과 같은 책 (한 권으로 보고 검사에서 뺌)


class ResultBadge(StrEnum):
    """화면에 표시할 대표 판정 1개. 판정 4개 축에서 서버가 계산한다 (services/verification.py의 result_badge).

    프론트엔드는 이 값을 그대로 표시한다. 우선순위는 위에서부터.
    """

    MISPLACED_CONFIRMED = "MISPLACED_CONFIRMED"  # 오배가 확정
    MISPLACED_SUSPECTED = "MISPLACED_SUSPECTED"  # 오배가 의심
    OUT_OF_PLACE = "OUT_OF_PLACE"                # 엉뚱한 책 (청구기호 흐름에서 분류가 다른 책)
    NOT_OBSERVED = "NOT_OBSERVED"                # 스캔 완료 범위에서 미관측 (분실 아님)
    RECOGNITION_ISSUE = "RECOGNITION_ISSUE"      # 인식 실패·불확실·미등록 도서
    OUT_OF_ORDER = "OUT_OF_ORDER"                # 순서만 어긋남 (같은 분류 근처)
    LOCATION_UNDETERMINED = "LOCATION_UNDETERMINED"  # 책은 알아봤으나 위치 판정 불가 (후보가 여러 칸에 걸침, 등록 위치 없음)
    SCAN_INCOMPLETE = "SCAN_INCOMPLETE"          # 스캔 중단으로 판단 보류
    NORMAL = "NORMAL"                            # 제자리


class ObservationStatus(StrEnum):
    """스캔 범위 기준 관측 상태 (BE-007)."""

    OBSERVED = "OBSERVED"
    NOT_OBSERVED = "NOT_OBSERVED"        # 스캔 완료 범위인데 관측되지 않음 (분실 처리 아님)
    SCAN_INCOMPLETE = "SCAN_INCOMPLETE"  # 스캔이 끝나지 않아 판단 불가


class WorkflowStatus(StrEnum):
    """관리자 처리 흐름 상태 (BE-008, BE-014)."""

    REOBSERVATION_PENDING = "REOBSERVATION_PENDING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    CLOSED = "CLOSED"


class LoanStatus(StrEnum):
    """대출 상태 (BE-002)."""

    AVAILABLE = "AVAILABLE"
    CHECKED_OUT = "CHECKED_OUT"
    IN_TRANSIT = "IN_TRANSIT"
    UNKNOWN = "UNKNOWN"


class RobotTaskStatus(StrEnum):
    """로봇 작업 상태 (BE-010)."""

    PENDING_APPROVAL = "PENDING_APPROVAL"  # 관리자 승인 전. 로봇에 전달하지 않는다.
    PENDING = "PENDING"                    # 로봇이 가져가기를 기다림
    ACCEPTED = "ACCEPTED"                  # 로봇이 수락 (완료 아님)
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"                    # 결과 불명. 실패로 간주하거나 자동 재전송하지 않는다.


class RobotTaskType(StrEnum):
    """로봇 작업 종류 (기획서 9장). [가정] 로봇 담당자와 최종 확정 전."""

    MOVE_TO_SHELF = "MOVE_TO_SHELF"
    SCAN_SHELF = "SCAN_SHELF"
    REOBSERVE_BOOK = "REOBSERVE_BOOK"
    RETRIEVE_MISPLACED_BOOK = "RETRIEVE_MISPLACED_BOOK"  # 인출 + 바구니 보관을 하나의 상위 작업으로 관리
    CANCEL_TASK = "CANCEL_TASK"


class RobotConnectionStatus(StrEnum):
    """로봇 통신 상태 (BE-009)."""

    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class PhysicalLocationType(StrEnum):
    """도서의 현재 물리 위치 종류 (BE-012). [가정] 값 이름은 협의 후 변경 가능."""

    SHELF = "SHELF"
    ROBOT_GRIPPER = "ROBOT_GRIPPER"
    BASKET = "BASKET"
    COLLECTED_BY_STAFF = "COLLECTED_BY_STAFF"
    UNKNOWN = "UNKNOWN"


class LocationCertainty(StrEnum):
    """물리 위치의 확정 여부. 로봇이 확인하지 않은 위치는 CONFIRMED로 기록하지 않는다."""

    CONFIRMED = "CONFIRMED"
    ESTIMATED = "ESTIMATED"


class OcrStatus(StrEnum):
    """로봇이 올린 사진 1장의 AI OCR 처리 상태."""

    PENDING = "PENDING"          # AI에 보내기 전 (AI 서버 주소가 없으면 AI가 결과를 보내 줄 때까지 이 상태)
    PROCESSING = "PROCESSING"    # AI가 처리 중
    DONE = "DONE"                # 결과를 받아 책마다 관측을 만듦
    FAILED = "FAILED"            # AI 호출 실패 또는 결과 형식 오류 (관리자가 다시 시도 가능)


class ScanSessionStatus(StrEnum):
    """스캔 세션 진행 상태 (BE-007). [가정]"""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"    # 범위 전체 촬영 완료 → 미관측 판정 가능
    INCOMPLETE = "INCOMPLETE"  # 중단됨 → 미관측 판정하지 않음 (SCAN_INCOMPLETE)


class AlertType(StrEnum):
    """관리자 알림 종류 (BE-013). [가정]"""

    MISPLACED_SUSPECTED = "MISPLACED_SUSPECTED"        # 재관측을 반복해도 의심 상태
    MISPLACED_CONFIRMED = "MISPLACED_CONFIRMED"        # 오배가 확정
    OUT_OF_PLACE = "OUT_OF_PLACE"                      # 엉뚱한 책 (청구기호 흐름 검사)
    RECOGNITION_FAILED = "RECOGNITION_FAILED"          # 인식 실패·불확실 반복
    NOT_OBSERVED = "NOT_OBSERVED"                      # 스캔 완료 범위에서 미관측
    REVIEW_REQUIRED = "REVIEW_REQUIRED"                # 기타 확인 필요 (미등록 도서, 대출 중 도서 관측 등)
    RETRIEVAL_APPROVAL_REQUIRED = "RETRIEVAL_APPROVAL_REQUIRED"  # 인출 승인 대기
    TASK_FAILED = "TASK_FAILED"
    TASK_RESULT_UNKNOWN = "TASK_RESULT_UNKNOWN"
    COMMUNICATION_ERROR = "COMMUNICATION_ERROR"        # 로봇 통신 끊김


class AlertStatus(StrEnum):
    """알림 확인 상태 (BE-013). [가정]"""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class AdminActionType(StrEnum):
    """관리자 처리 종류 (BE-014). [가정]"""

    REQUEST_REOBSERVATION = "REQUEST_REOBSERVATION"
    APPROVE_RETRIEVAL = "APPROVE_RETRIEVAL"
    REJECT_RETRIEVAL = "REJECT_RETRIEVAL"
    CANCEL_TASK = "CANCEL_TASK"
    CONFIRM_ALERT = "CONFIRM_ALERT"
    RESOLVE_ALERT = "RESOLVE_ALERT"
    CHANGE_REGISTERED_LOCATION = "CHANGE_REGISTERED_LOCATION"
    MARK_COLLECTED = "MARK_COLLECTED"  # 바구니 도서 직원 회수


class EventSource(StrEnum):
    """작업 이벤트를 발생시킨 주체."""

    BACKEND = "BACKEND"
    ROBOT = "ROBOT"
    ADMIN = "ADMIN"


class DeviceType(StrEnum):
    """API 키를 쓰는 외부 장치 종류 (BE-017)."""

    AI = "AI"
    ROBOT = "ROBOT"

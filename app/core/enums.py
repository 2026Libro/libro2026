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


class ScanSessionStatus(StrEnum):
    """스캔 세션 진행 상태 (BE-007). [가정]"""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"    # 범위 전체 촬영 완료 → 미관측 판정 가능
    INCOMPLETE = "INCOMPLETE"  # 중단됨 → 미관측 판정하지 않음 (SCAN_INCOMPLETE)


class AlertType(StrEnum):
    """관리자 알림 종류 (BE-013). [가정]"""

    MISPLACED_SUSPECTED = "MISPLACED_SUSPECTED"
    RECOGNITION_FAILED = "RECOGNITION_FAILED"
    NOT_OBSERVED = "NOT_OBSERVED"
    TASK_FAILED = "TASK_FAILED"
    TASK_RESULT_UNKNOWN = "TASK_RESULT_UNKNOWN"
    COMMUNICATION_ERROR = "COMMUNICATION_ERROR"


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

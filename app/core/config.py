"""환경변수(.env) 기반 설정.

DB 접속 정보·API 키 등 민감한 값은 코드에 직접 쓰지 않고 .env 파일에서 읽는다.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Library Robot Backend"
    app_env: str = "development"

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str
    db_password: str
    db_name: str

    # [가정] 프론트엔드 개발 서버 주소 (React/Vite 기본 포트). 쉼표로 여러 개. 프론트 담당자와 협의 후 조정.
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"

    # [가정] 프론트엔드 로그인 방식이 정해지기 전까지 쓰는 관리자 토큰. 형식: "관리자ID:토큰,관리자ID2:토큰2"
    admin_tokens: str = ""

    # [가정] 위치 검증 임계값. AI 담당자와 협의 후 .env에서 조정한다.
    verify_min_detection: float = 0.5        # 이보다 낮으면 책등 탐지 자체를 믿지 않음 → FAILED
    verify_min_ocr: float = 0.6              # 이보다 낮으면 읽은 글자를 믿지 않음
    verify_min_identification: float = 0.8   # AI가 보낸 book_copy_id를 IDENTIFIED로 인정하는 최소값
    verify_max_reobservations: int = 2       # 재관측을 이만큼 해도 해결되지 않으면 관리자 검토
    auto_reobservation: bool = True          # 재관측 대기 판정이 나오면 REOBSERVE_BOOK 작업을 자동 생성
    # 오배가 확정 시 인출 요청(PENDING_APPROVAL)을 자동 생성. 승인 전에는 로봇에 전달되지 않는다.
    auto_retrieval_request: bool = True
    # [가정] 로봇이 재관측 촬영을 끝낸 뒤 AI 결과를 기다리는 최대 시간. 지나면 관리자 검토로 넘긴다.
    reobservation_result_wait_seconds: int = 300
    # 대출 상태를 판정에 쓸지. 도서관 대출 정보를 실시간으로 받을 수 없어 기본은 쓰지 않는다
    # (DB의 대출 상태는 장서를 가져온 시점 기록일 뿐). false면:
    #  - 서가에서 보인 책이 '대출 중'으로 되어 있어도 관리자 검토로 보내지 않는다
    #  - 서가에서 안 보인 책(미관측)은 대출 중일 수 있으므로 관리자 검토로 보내지 않는다 (참고 기록만)
    #  - 인출 승인 조건에서 대출 상태를 보지 않는다 (로봇이 서가에서 확인한 위치로 판단)
    loan_status_reliable: bool = False

    # [가정] 관측 이미지 보관 폴더. 관측의 image_ref(상대 경로)를 이 폴더 기준으로 찾는다.
    # 실제 보관 방식(AI 서버 공유 폴더, 백엔드 업로드 등)은 AI 담당자와 협의 후 정한다.
    image_root: str = "data/images"

    # [가정] 로봇 통신 기준. 로봇 담당자와 상태 보고 주기를 협의 후 조정한다.
    robot_offline_after_seconds: int = 60    # 마지막 통신 후 이 시간이 지나면 OFFLINE, 진행 중 작업은 UNKNOWN
    task_watchdog_interval_seconds: int = 10 # 위 검사를 서버가 몇 초마다 할지

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_token_map(self) -> dict[str, str]:
        """{토큰: 관리자ID}"""
        result = {}
        for pair in self.admin_tokens.split(","):
            admin_id, sep, token = pair.strip().partition(":")
            if sep and admin_id and token:
                result[token] = admin_id
        return result

    @property
    def database_url(self) -> URL:
        # URL.create를 쓰면 비밀번호에 @, %, # 등 특수문자가 있어도 안전하게 처리된다.
        return URL.create(
            drivername="mysql+pymysql",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

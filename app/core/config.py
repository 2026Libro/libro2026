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

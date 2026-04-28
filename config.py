import os

from pydantic_settings import BaseSettings, SettingsConfigDict

# Allow overriding .env path via environment variable:
#   ENV_FILE=/opt/nms/port_monitoring/monitoring-server/.env python app.py
_env_file = os.environ.get("ENV_FILE", ".env")


class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int = 3306
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str = "port_monitoring"
    NMS_DB_NAME: str = "nms"
    MASTER_KEY: str
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 9000
    STALE_AGENT_MINUTES: int = 5
    TIMESTAMP_TOLERANCE_SECONDS: int = 600
    DATA_RETENTION_DAYS: int = 0   # 0 = disabled (keep forever)
    RATE_LIMIT_RPM: int = 30       # max requests per minute per client IP; 0 = disabled
    DB_POOL_SIZE: int = 30         # base connection pool size
    DB_MAX_OVERFLOW: int = 70      # extra connections allowed beyond pool_size

    model_config = SettingsConfigDict(env_file=_env_file, env_file_encoding="utf-8")


settings = Settings()

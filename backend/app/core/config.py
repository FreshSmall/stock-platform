"""Application configuration loaded from environment variables / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed settings sourced from the environment.

    Required fields without defaults: ``db_host``, ``db_user``,
    ``db_password``, ``llm_api_key``, ``jwt_secret``. All other fields have
    sensible defaults so a minimal ``.env`` is enough to boot the service.
    """

    db_host: str
    db_port: int = 3306
    db_user: str
    db_password: str
    db_name: str = "stock_analysis"

    llm_api_key: str
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com/v1"

    jwt_secret: str
    jwt_alg: str = "HS256"
    jwt_exp_minutes: int = 1440

    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()

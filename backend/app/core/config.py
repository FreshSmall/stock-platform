"""Application configuration loaded from environment variables / .env file."""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict

# DeepSeek / 通义 / 大多数国内 LLM 服务走直连更快更稳。开发机常会开启
# 系统级代理（如 Clash 的 all_proxy=socks5://127.0.0.1:7897），这会让
# httpx/openai SDK 尝试走 SOCKS 代理，触发
# "Using SOCKS proxy, but the 'socksio' package is not installed" 报错。
# 这里显式清理代理环境变量，让 LLM 调用直连。
for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                   "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_proxy_var, None)


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

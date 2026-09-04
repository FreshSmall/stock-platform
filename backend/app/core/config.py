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

# 清掉环境变量还不够：requests/urllib 在 macOS 上会另读「系统代理」
# （scutil --proxy，即 Clash 的"系统代理"开关）。本服务访问的全部是
# 国内数据源/LLM，不需要也不应走代理——本地代理对这些域名大量拒连
# （2026-08-14/16 两次日K批量同步因此部分失败）。urllib 的
# proxy_bypass 把 ``no_proxy='*'`` 视为全部绕过，且优先级高于系统代理，
# 所以这里 setdefault 一个通配（想为个别域名走代理时可覆盖此值）。
os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("NO_PROXY", "*")


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

    # --- Multi-year daily-K history back-fill (polled, low-rate) -----------
    # 防封策略（2026-08-16 曾观测到 ~2000 请求突发后腾讯 WAF 501 挑战、东财
    # IP 封禁数小时）：小批量 + 低频轮询 + 请求间隔节流（见 history_backfill
    # 模块头）。每轮约 15 只 × ~5 块 ≈ 75 个请求、摊在 ~2.5 分钟里，峰值低于
    # 1 req/s；且避开 17:15–18:45 的每日全市场同步窗口。全量 ~4200 只补齐约
    # 需 2~3 天，进度持久化在 sa_history_sync_state。
    history_backfill_enabled: bool = True
    history_years: int = 5
    history_batch_size: int = 15          # stocks per polling tick
    history_poll_minutes: int = 10        # polling interval

    # --- V2.1 数据修复（spec-004）-------------------------------------------
    # 复权读取切换开关：legacy=daily_prices(qfq，现状)；v2=sa_kline_daily(raw)
    # + sa_adjust_factor 按需折算。灰度校验通过后切 v2，出问题改回 legacy 即回滚。
    kline_source: str = "legacy"
    # sa_kline_daily 全量重灌 tick（与 history_backfill 同一防封节奏），污染
    # 清单（priority=0）优先出队。
    kline_rebuild_enabled: bool = False   # D1 启动重灌时手动置 True
    kline_rebuild_batch_size: int = 15
    kline_rebuild_poll_minutes: int = 10
    # 每日数据质量巡检（08:00）。
    quality_check_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()

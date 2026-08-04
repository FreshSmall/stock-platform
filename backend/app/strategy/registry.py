"""Strategy registry: name -> (class, metadata).

Metadata describes each strategy for the ``/strategy`` list endpoint:

- name:        machine key (ma, macd, ...)
- title:       display name
- description: short prose
- params:      list of ``{name, type, default, min, max, description}``
- available:   whether usable in V1 (True) or greyed-out (False)
- cls:         the backtrader ``Strategy`` class (None for unavailable V2
               placeholders)
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategyMeta:
    name: str
    title: str
    description: str
    params: list[dict[str, Any]] = field(default_factory=list)
    available: bool = True
    cls: Any = (
        None  # the backtrader Strategy class (None for unavailable V2 strategies)
    )


_REGISTRY: dict[str, StrategyMeta] = {}


def register(meta: StrategyMeta) -> None:
    """Insert/replace ``meta`` in the registry keyed by ``meta.name``."""
    _REGISTRY[meta.name] = meta


def get(name: str) -> StrategyMeta | None:
    """Return the metadata for ``name`` or ``None`` if unknown."""
    return _REGISTRY.get(name)


def all_strategies() -> list[StrategyMeta]:
    """Every registered strategy, available or not."""
    return list(_REGISTRY.values())


def available_strategies() -> list[StrategyMeta]:
    """Only the V1-available strategies (``available=True``)."""
    return [m for m in _REGISTRY.values() if m.available]


# --- Built-in strategy registrations ---------------------------------------

from app.strategy.ma_strategy import MaStrategy  # noqa: E402

register(
    StrategyMeta(
        name="ma",
        title="MA 均线策略",
        description="基于快慢均线金叉/死叉的趋势跟踪策略。金叉买入，死叉卖出。",
        params=[
            {
                "name": "fast",
                "type": "int",
                "default": 5,
                "min": 2,
                "max": 60,
                "description": "快线周期",
            },
            {
                "name": "slow",
                "type": "int",
                "default": 20,
                "min": 5,
                "max": 250,
                "description": "慢线周期",
            },
        ],
        available=True,
        cls=MaStrategy,
    )
)

from app.strategy.macd_strategy import MacdStrategy  # noqa: E402

register(
    StrategyMeta(
        name="macd",
        title="MACD 策略",
        description=(
            "基于 MACD 金叉/死叉的趋势策略，附简单顶背离减仓保护。"
            "金叉买入，死叉/顶背离卖出。"
        ),
        params=[
            {
                "name": "period_me1",
                "type": "int",
                "default": 12,
                "min": 2,
                "max": 60,
                "description": "快线EMA周期",
            },
            {
                "name": "period_me2",
                "type": "int",
                "default": 26,
                "min": 5,
                "max": 120,
                "description": "慢线EMA周期",
            },
            {
                "name": "period_signal",
                "type": "int",
                "default": 9,
                "min": 2,
                "max": 60,
                "description": "信号线周期",
            },
        ],
        available=True,
        cls=MacdStrategy,
    )
)

# V2 strategies — now implemented and available.
from app.strategy.ema_strategy import EmaStrategy  # noqa: E402
from app.strategy.trend_strategy import TrendStrategy  # noqa: E402
from app.strategy.leader_strategy import LeaderStrategy  # noqa: E402
from app.strategy.board_strategy import BoardStrategy  # noqa: E402
from app.strategy.lowbuy_strategy import LowBuyStrategy  # noqa: E402
from app.strategy.breakout_strategy import BreakoutStrategy  # noqa: E402

register(
    StrategyMeta(
        name="ema", title="EMA 策略",
        description="指数移动均线金叉/死叉。EMA12 上穿 EMA26 买入，下穿卖出。",
        params=[
            {"name": "fast", "type": "int", "default": 12, "min": 2, "max": 60, "description": "快线EMA周期"},
            {"name": "slow", "type": "int", "default": 26, "min": 5, "max": 120, "description": "慢线EMA周期"},
        ],
        available=True, cls=EmaStrategy,
    )
)
register(
    StrategyMeta(
        name="trend", title="趋势策略",
        description="ADX>阈值确认强趋势 + MA多头排列入场，趋势破坏离场。",
        params=[
            {"name": "adx_period", "type": "int", "default": 14, "min": 5, "max": 50, "description": "ADX周期"},
            {"name": "adx_threshold", "type": "number", "default": 25, "min": 10, "max": 50, "description": "ADX强度阈值"},
            {"name": "fast", "type": "int", "default": 5, "min": 2, "max": 60, "description": "快线MA周期"},
            {"name": "slow", "type": "int", "default": 20, "min": 5, "max": 250, "description": "慢线MA周期"},
        ],
        available=True, cls=TrendStrategy,
    )
)
register(
    StrategyMeta(
        name="leader", title="龙头策略",
        description="放量暴涨(涨幅>阈值+量比>阈值)入场，跌破MA离场。",
        params=[
            {"name": "gain_threshold", "type": "number", "default": 5.0, "min": 1, "max": 20, "description": "涨幅阈值%"},
            {"name": "vol_period", "type": "int", "default": 5, "min": 2, "max": 20, "description": "均量周期"},
            {"name": "vol_ratio", "type": "number", "default": 1.5, "min": 1, "max": 5, "description": "量比阈值"},
            {"name": "ma_period", "type": "int", "default": 10, "min": 5, "max": 60, "description": "止盈MA周期"},
        ],
        available=True, cls=LeaderStrategy,
    )
)
register(
    StrategyMeta(
        name="board", title="打板策略",
        description="触及涨停(涨幅>=9.5%)入场，次日走弱或止损离场。",
        params=[
            {"name": "limit_threshold", "type": "number", "default": 9.5, "min": 5, "max": 20, "description": "涨停阈值%"},
            {"name": "stop_pct", "type": "number", "default": 5.0, "min": 1, "max": 15, "description": "止损%"},
        ],
        available=True, cls=BoardStrategy,
    )
)
register(
    StrategyMeta(
        name="lowbuy", title="低吸策略",
        description="回踩MA支撑(均线之下3%内)+缩量入场，止盈/止损离场。",
        params=[
            {"name": "ma_period", "type": "int", "default": 20, "min": 5, "max": 60, "description": "支撑MA周期"},
            {"name": "pullback_pct", "type": "number", "default": 3.0, "min": 1, "max": 10, "description": "回踩幅度%"},
            {"name": "vol_ratio", "type": "number", "default": 0.8, "min": 0.3, "max": 1.5, "description": "缩量比阈值"},
            {"name": "profit_pct", "type": "number", "default": 8.0, "min": 2, "max": 30, "description": "止盈%"},
            {"name": "stop_pct", "type": "number", "default": 5.0, "min": 1, "max": 15, "description": "止损%"},
        ],
        available=True, cls=LowBuyStrategy,
    )
)
register(
    StrategyMeta(
        name="breakout", title="突破策略",
        description="突破布林带上轨+放量入场，跌破中轨或止损离场。",
        params=[
            {"name": "boll_period", "type": "int", "default": 20, "min": 5, "max": 50, "description": "布林周期"},
            {"name": "boll_dev", "type": "number", "default": 2.0, "min": 1, "max": 4, "description": "布林标准差倍数"},
            {"name": "vol_ratio", "type": "number", "default": 1.5, "min": 1, "max": 5, "description": "放量比阈值"},
            {"name": "stop_pct", "type": "number", "default": 5.0, "min": 1, "max": 15, "description": "止损%"},
        ],
        available=True, cls=BreakoutStrategy,
    )
)

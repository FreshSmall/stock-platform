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

# V2 strategies pre-registered as unavailable (greyed out in UI)
for _name, _title, _desc in [
    ("ema", "EMA 策略", "指数移动均线金叉/死叉"),
    ("trend", "趋势策略", "ADX/SuperTrend 趋势跟踪"),
    ("leader", "龙头策略", "板块龙头追踪"),
    ("board", "打板策略", "涨停板打板"),
    ("lowbuy", "低吸策略", "缩量回踩低吸"),
    ("breakout", "突破策略", "放量突破"),
]:
    register(
        StrategyMeta(
            name=_name, title=_title, description=_desc, available=False, cls=None
        )
    )

del _name, _title, _desc

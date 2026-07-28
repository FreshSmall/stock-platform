"""Strategy package — base class, built-in strategies, and registry."""

from app.strategy.base import BaseStrategy  # noqa: F401
from app.strategy.ma_strategy import MaStrategy  # noqa: F401
from app.strategy.macd_strategy import MacdStrategy  # noqa: F401

# Importing registry executes the registrations.
from app.strategy import registry  # noqa: F401
from app.strategy.registry import (  # noqa: F401
    StrategyMeta,
    all_strategies,
    available_strategies,
    get,
)

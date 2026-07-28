"""ORM model package.

Importing :mod:`app.models` registers every mapping with the declarative
metadata, so a single ``from app.models import Base`` is enough to make all
tables visible to ``Base.metadata`` (and thus to Alembic autogenerate).

The ``stock_`` / ``daily_prices`` tables are read-only mappings of data
populated by external pipelines; the ``sa_``-prefixed tables are owned and
written by this service.
"""

from app.core.database import Base
from app.models.ai import SaAiAnalysis, SaAiChatMessage, SaAiChatSession
from app.models.backtest import SaBacktestResult, SaBacktestRun
from app.models.finance import SaFinancialExtra, SaMoneyFlow
from app.models.stock import DailyPrice, StockPool
from app.models.user import SaUser

__all__ = [
    "Base",
    # read-only mappings of existing external tables
    "DailyPrice",
    "StockPool",
    # application-managed sa_ tables
    "SaUser",
    "SaAiAnalysis",
    "SaAiChatSession",
    "SaAiChatMessage",
    "SaBacktestRun",
    "SaBacktestResult",
    "SaMoneyFlow",
    "SaFinancialExtra",
]

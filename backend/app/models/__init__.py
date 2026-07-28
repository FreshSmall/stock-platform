"""ORM model package.

Importing :mod:`app.models` registers every mapping with the declarative
metadata, so a single ``from app.models import Base, DailyPrice, StockPool``
is enough to make the tables visible to ``Base.metadata``.
"""

from app.core.database import Base
from app.models.stock import DailyPrice, StockPool

__all__ = ["Base", "DailyPrice", "StockPool"]

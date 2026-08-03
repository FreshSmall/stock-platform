"""Factor computation framework (V2 BP-V2-001).

A *factor* is a numeric signal computed per (stock, trade_date) used to
explain or predict future returns. Every factor implements the same interface
(:class:`Factor`) and is registered in :data:`registry` so the rest of the
platform can enumerate/compute/score factors uniformly by code.

Design:
- Technical factors (trend/momentum/volatility/volume) compute on the fly via
  the V1.5 :mod:`indicator_service` — no pre-materialisation needed.
- Fundamental/sentiment factors read existing tables (sa_financial_extra /
  sa_market_sentiment / sa_north_flow) populated by V1/V1.5 sync jobs.
- Factor *values* are only persisted to ``sa_factor_value`` when needed by IC
  testing or multi-factor scoring (avoids pre-computing the whole market).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class FactorParam:
    """A tunable parameter of a factor (name/type/default/range)."""

    name: str
    default: Any
    type: str = "number"
    min: float | None = None
    max: float | None = None
    description: str = ""


class Factor(ABC):
    """Base class for every factor.

    Subclasses set the class attributes ``code``/``name``/``category``/``params``
    and implement :meth:`compute`. The registry instantiates factors once and
    reuses the instances (factors are stateless beyond their params).
    """

    code: str = ""
    name: str = ""
    category: str = ""  # trend/momentum/volatility/volume/fundamental/sentiment
    params: list[FactorParam] = field(default_factory=list)

    @abstractmethod
    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        """Return the factor value for ``stock`` on ``trade_date``.

        ``None`` means "not applicable / insufficient data" — callers should
        treat it as missing rather than 0.
        """

    def default_kwargs(self) -> dict[str, Any]:
        """The factor's parameters keyed by name, at their defaults."""
        return {p.name: p.default for p in self.params}


class FactorRegistry:
    """Registry of all factors, keyed by ``code``."""

    def __init__(self) -> None:
        self._factors: dict[str, Factor] = {}

    def register(self, factor: Factor) -> None:
        if not factor.code:
            raise ValueError("factor has no code")
        if factor.code in self._factors:
            logger.warning("factor %s already registered, overwriting", factor.code)
        self._factors[factor.code] = factor

    def get(self, code: str) -> Factor | None:
        return self._factors.get(code)

    def all_factors(self) -> list[Factor]:
        return list(self._factors.values())

    def by_category(self, category: str) -> list[Factor]:
        return [f for f in self._factors.values() if f.category == category]

    def categories(self) -> list[str]:
        return sorted({f.category for f in self._factors.values()})

    def codes(self) -> list[str]:
        return sorted(self._factors.keys())


# The single process-wide registry. ``app.factor.__init__`` imports each
# factor module so they self-register on import.
registry = FactorRegistry()

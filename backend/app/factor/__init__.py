"""Factor package.

Importing :mod:`app.factor` registers every built-in factor with the registry,
so a single ``from app.factor import registry`` makes them all visible.
"""

from app.factor.base import Factor, FactorParam, FactorRegistry, registry

# Self-registering factor modules. Import order does not matter; each module
# constructs its factor(s) and calls registry.register(...) at import time.
from app.factor import (  # noqa: F401,E402
    fundamental,
    momentum,
    sentiment,
    trend,
    volatility,
    volume,
)

__all__ = ["Factor", "FactorParam", "FactorRegistry", "registry"]

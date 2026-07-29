"""Re-export the declarative base for convenience.

The :class:`~app.core.database.Base` class itself lives in
``app.core.database``; this module simply re-exports it so import sites can
write ``from app.models.base import Base`` regardless of where the engine
session wiring lives.
"""

from app.core.database import Base  # noqa: F401

"""ORM mappings for the V2.1 data-quality patrol tables.

* :class:`SaDataQualityRule`  - configurable thresholds per check/metric.
* :class:`SaDataQualityCheck` - one row per (day, check, metric) result.

Checks run daily at 08:00 (``quality_check`` scheduler job). Each check knows
its own comparison direction (some fail high, some fail low); the rule table
only stores the numbers so thresholds can be tuned without a deploy.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaDataQualityRule(Base):
    """Threshold configuration for one quality-check metric.

    Maps the ``sa_data_quality_rule`` table. ``check_name`` is one of
    ``adjustment_break / frozen / row_baseline / field_missing / coverage /
    amplitude_anomaly``; ``metric_name`` names the concrete number compared
    against the thresholds. Threshold semantics (fail-high vs fail-low) are
    owned by the check implementation in ``app.services.quality_service``.
    """

    __tablename__ = "sa_data_quality_rule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_name: Mapped[str] = mapped_column(String(50), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(50), nullable=False)
    warn_threshold: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    fail_threshold: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    enabled: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("check_name", "metric_name", name="uk_check_metric"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaDataQualityRule(id={self.id!r}, check_name={self.check_name!r}, "
            f"metric_name={self.metric_name!r})"
        )


class SaDataQualityCheck(Base):
    """One materialized quality-check result for one trade day.

    Maps the ``sa_data_quality_check`` table. ``status`` is
    ``pass / warn / fail``; ``detail`` carries a bounded stock-level anomaly
    list so the admin page can drill down without re-running the check.
    Re-running a day upserts (replace) its rows.
    """

    __tablename__ = "sa_data_quality_check"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_name: Mapped[str] = mapped_column(String(50), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(50), nullable=False)
    metric_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    detail: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("check_date", "check_name", "metric_name", name="uk_date_check_metric"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaDataQualityCheck(id={self.id!r}, check_date={self.check_date!r}, "
            f"check_name={self.check_name!r}, status={self.status!r})"
        )

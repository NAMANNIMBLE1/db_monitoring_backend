"""Global thresholds used by host_alert_scorer to generate alerts.

Single-row table keyed by ``metric`` ('disk', 'cpu', 'memory').  Values are
percentages.  ``forecast_days`` is shared across all metrics and stored as
the row for metric='disk'.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, Numeric, String

from models import Base


class AlertThreshold(Base):
    __tablename__ = "alert_threshold"

    metric = Column(String(16), primary_key=True)  # disk | cpu | memory
    warn_threshold = Column(Numeric(5, 2), nullable=False)
    critical_threshold = Column(Numeric(5, 2), nullable=False)
    forecast_days = Column(Integer, nullable=False, server_default="15")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "warn_threshold": float(self.warn_threshold) if self.warn_threshold is not None else None,
            "critical_threshold": float(self.critical_threshold) if self.critical_threshold is not None else None,
            "forecast_days": self.forecast_days,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

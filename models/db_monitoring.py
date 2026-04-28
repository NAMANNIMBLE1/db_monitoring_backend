"""Database monitoring models.

Stores per-instance DB metrics collected by agents.  The ``metrics``
column holds a JSON blob with all 11 monitoring categories so new
metrics can be added without schema migrations.
"""

import json
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

from models import Base


class DbMonitoring(Base):
    __tablename__ = "db_monitoring"
    __table_args__ = (
        UniqueConstraint(
            "ip_address", "timestamp", "instance_name",
            name="uq_db_ip_ts_instance",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ip_address = Column(String(45), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    db_type = Column(String(20), nullable=False, index=True)
    instance_name = Column(String(128), nullable=False, index=True)
    db_host = Column(String(255))
    db_port = Column(Integer)
    is_reachable = Column(Boolean)
    health_score = Column(Numeric(5, 2))
    metrics = Column(Text)  # JSON blob

    def __repr__(self) -> str:
        return (
            f"<DbMonitoring(ip={self.ip_address!r}, instance={self.instance_name!r}, "
            f"timestamp={self.timestamp!r})>"
        )

    def to_dict(self) -> dict:
        metrics_parsed = None
        if self.metrics is not None:
            try:
                metrics_parsed = json.loads(self.metrics)
            except (json.JSONDecodeError, ValueError):
                metrics_parsed = {}

        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "db_type": self.db_type,
            "instance_name": self.instance_name,
            "db_host": self.db_host,
            "db_port": self.db_port,
            "is_reachable": self.is_reachable,
            "health_score": float(self.health_score) if self.health_score is not None else None,
            "metrics": metrics_parsed,
        }


class DbMonitoringAlert(Base):
    __tablename__ = "db_monitoring_alert"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ip_address = Column(String(45), nullable=False, index=True)
    instance_name = Column(String(128), nullable=True, index=True)
    alert_type = Column(String(64), nullable=False)
    severity = Column(String(16), nullable=False, server_default="warning")
    message = Column(Text)
    details = Column(Text)  # JSON
    is_resolved = Column(Boolean, nullable=False, server_default="0")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime)

    def __repr__(self) -> str:
        return (
            f"<DbMonitoringAlert(ip={self.ip_address!r}, "
            f"instance={self.instance_name!r}, type={self.alert_type!r})>"
        )

    def to_dict(self) -> dict:
        details_parsed = None
        if self.details is not None:
            try:
                details_parsed = json.loads(self.details)
            except (json.JSONDecodeError, ValueError):
                details_parsed = {}

        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "instance_name": self.instance_name,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "message": self.message,
            "details": details_parsed,
            "is_resolved": self.is_resolved,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }

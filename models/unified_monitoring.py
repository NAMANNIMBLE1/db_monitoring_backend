from sqlalchemy import BigInteger, Column, DateTime, Integer, Numeric, String, Text, UniqueConstraint

from models import Base


class UnifiedMonitoring(Base):
    __tablename__ = "unified_monitoring"
    __table_args__ = (
        UniqueConstraint("ip_address", "timestamp", name="uq_unified_ip_timestamp"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ip_address = Column(String(45), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    cpu_usage = Column(Numeric(5, 2))
    memory_usage = Column(Numeric(5, 2))
    disk_usage = Column(Numeric(5, 2))
    load_average = Column(Numeric(7, 2))    # Linux only, NULL for Windows
    uptime = Column(Integer)                 # Linux only
    event_log_errors = Column(Integer)       # Windows only
    services = Column(Text)                  # JSON: {"winrm":"UP","nginx":"DOWN",...}

    def __repr__(self) -> str:
        return (
            f"<UnifiedMonitoring(ip={self.ip_address!r}, "
            f"timestamp={self.timestamp!r})>"
        )

    def to_dict(self) -> dict:
        import json

        services_parsed = None
        if self.services is not None:
            try:
                services_parsed = json.loads(self.services)
            except (json.JSONDecodeError, ValueError):
                services_parsed = {}

        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "cpu_usage": float(self.cpu_usage) if self.cpu_usage is not None else None,
            "memory_usage": float(self.memory_usage) if self.memory_usage is not None else None,
            "disk_usage": float(self.disk_usage) if self.disk_usage is not None else None,
            "load_average": float(self.load_average) if self.load_average is not None else None,
            "uptime": self.uptime,
            "event_log_errors": self.event_log_errors,
            "services": services_parsed,
        }

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Enum, Boolean

from models import Base


class MonitoredDevice(Base):
    """Local copy of devices, synced from nms.devices + agents that register
    but are not in the NMS device list.

    This table lives in port_monitoring DB so we never write to nms DB.
    """

    __tablename__ = "monitored_device"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hostname = Column(String(255), nullable=False)
    ip_address = Column(String(45), unique=True, nullable=False)
    source = Column(
        Enum("nms", "agent", name="device_source"),
        nullable=False,
        default="nms",
        comment="'nms' = synced from nms.devices, 'agent' = auto-registered by agent",
    )
    is_active = Column(Boolean, default=True, nullable=False)
    synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "source": self.source,
            "is_active": self.is_active,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

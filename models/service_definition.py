from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Integer,
    String,
    Text,
)

from models import Base


class ServiceDefinition(Base):
    __tablename__ = "service_definition"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(64), unique=True, nullable=False, index=True)
    display_name = Column(String(128), nullable=False)
    category = Column(String(64), nullable=False)
    os_type = Column(
        Enum("windows", "linux", "both", name="os_type_enum"),
        nullable=False,
        default="both",
    )
    check_type = Column(
        Enum("tcp", "udp", "service", "tcp_service", "udp_service", name="check_type_enum"),
        nullable=False,
        default="tcp",
    )
    tcp_ports = Column(Text, nullable=True)            # JSON array e.g. "[80, 443]"
    udp_ports = Column(Text, nullable=True)            # JSON array e.g. "[161]"
    win_service_names = Column(Text, nullable=True)    # JSON array e.g. '["W3SVC"]'
    linux_service_names = Column(Text, nullable=True)  # JSON array e.g. '["nginx"]'
    linux_process_names = Column(Text, nullable=True)  # JSON array e.g. '["nginx"]'
    is_default = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<ServiceDefinition(key={self.key!r}, display_name={self.display_name!r})>"

    def to_dict(self) -> dict:
        import json

        def _parse_json(val):
            if val is None:
                return []
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    return []
            return val

        return {
            "id": self.id,
            "key": self.key,
            "display_name": self.display_name,
            "category": self.category,
            "os_type": self.os_type,
            "check_type": self.check_type,
            "tcp_ports": _parse_json(self.tcp_ports),
            "udp_ports": _parse_json(self.udp_ports),
            "win_service_names": _parse_json(self.win_service_names),
            "linux_service_names": _parse_json(self.linux_service_names),
            "linux_process_names": _parse_json(self.linux_process_names),
            "is_default": self.is_default,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_agent_config(self) -> dict:
        """Return compact config for agent consumption."""
        import json

        def _parse_json(val):
            if val is None:
                return []
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    return []
            return val

        cfg = {
            "key": self.key,
            "check_type": self.check_type,
            "tcp_ports": _parse_json(self.tcp_ports),
            "udp_ports": _parse_json(self.udp_ports),
        }
        if self.os_type in ("windows", "both"):
            cfg["win_service_names"] = _parse_json(self.win_service_names)
        if self.os_type in ("linux", "both"):
            cfg["linux_service_names"] = _parse_json(self.linux_service_names)
            cfg["linux_process_names"] = _parse_json(self.linux_process_names)
        return cfg

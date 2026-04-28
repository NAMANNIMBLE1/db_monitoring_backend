from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String

from models import Base


class RegisteredAgent(Base):
    __tablename__ = "registered_agent"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(String(64), unique=True, nullable=False, index=True)
    ip_address = Column(String(45), unique=True, nullable=False, index=True)
    hostname = Column(String(255), nullable=False)
    os_type = Column(
        Enum("windows", "linux", name="os_type_enum"),
        default="windows",
        nullable=False,
    )
    agent_version = Column(String(32), default="1.0.0", nullable=False)
    registered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    auth_token_hash = Column(String(128), nullable=False)
    is_blocked = Column(Boolean, default=False, nullable=False, server_default="0")
    service_pack_id = Column(Integer, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<RegisteredAgent(agent_id={self.agent_id!r}, "
            f"ip={self.ip_address!r}, hostname={self.hostname!r})>"
        )

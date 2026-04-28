"""Service Pack models.

A Service Pack is a named collection of services that can be
assigned to multiple agents.  The Master Service Pack contains
all default services and cannot be deleted or modified.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from models import Base


class ServicePack(Base):
    __tablename__ = "service_pack"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)
    is_master = Column(Boolean, nullable=False, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<ServicePack(id={self.id}, name={self.name!r}, master={self.is_master})>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "is_master": self.is_master,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ServicePackItem(Base):
    __tablename__ = "service_pack_item"
    __table_args__ = (
        UniqueConstraint("pack_id", "service_key", name="uq_pack_service"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    pack_id = Column(Integer, ForeignKey("service_pack.id", ondelete="CASCADE"), nullable=False, index=True)
    service_key = Column(String(64), nullable=False)

    def __repr__(self) -> str:
        return f"<ServicePackItem(pack={self.pack_id}, key={self.service_key!r})>"

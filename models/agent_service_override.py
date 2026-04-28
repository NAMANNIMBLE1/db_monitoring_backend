"""Per-agent service configuration overrides.

Stores which services are enabled/disabled for a specific agent.
If no overrides exist for an agent, it gets all default active services.
"""

from sqlalchemy import Boolean, Column, Integer, String, UniqueConstraint

from models import Base


class AgentServiceOverride(Base):
    __tablename__ = "agent_service_override"
    __table_args__ = (
        UniqueConstraint("agent_id", "service_key", name="uq_agent_service"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(String(64), nullable=False, index=True)
    service_key = Column(String(64), nullable=False, index=True)
    is_enabled = Column(Boolean, nullable=False, server_default="1")

    def __repr__(self) -> str:
        return (
            f"<AgentServiceOverride(agent={self.agent_id!r}, "
            f"key={self.service_key!r}, enabled={self.is_enabled})>"
        )

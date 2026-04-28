from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, Numeric, String, UniqueConstraint

from models import Base


class WindowsPortMonitoring(Base):
    __tablename__ = "windows_port_monitoring"
    __table_args__ = (
        UniqueConstraint("ip_address", "timestamp", name="uq_ip_timestamp"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ip_address = Column(String(45), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    winrm_status = Column(String(16))
    rdp_status = Column(String(16))
    dns_status = Column(String(16))
    dhcp_status = Column(String(16))
    ad_ds_status = Column(String(16))
    w32time_status = Column(String(16))
    smb_status = Column(String(16))
    iis_status = Column(String(16))
    defender_status = Column(String(16))
    rpc_status = Column(String(16))
    sam_status = Column(String(16))
    lanmanserver_status = Column(String(16))
    cpu_usage = Column(Numeric(5, 2))
    memory_usage = Column(Numeric(5, 2))
    disk_usage = Column(Numeric(5, 2))
    event_log_errors = Column(Integer)

    def __repr__(self) -> str:
        return (
            f"<WindowsPortMonitoring(ip={self.ip_address!r}, "
            f"timestamp={self.timestamp!r})>"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "winrm_status": self.winrm_status,
            "rdp_status": self.rdp_status,
            "dns_status": self.dns_status,
            "dhcp_status": self.dhcp_status,
            "ad_ds_status": self.ad_ds_status,
            "w32time_status": self.w32time_status,
            "smb_status": self.smb_status,
            "iis_status": self.iis_status,
            "defender_status": self.defender_status,
            "rpc_status": self.rpc_status,
            "sam_status": self.sam_status,
            "lanmanserver_status": self.lanmanserver_status,
            "cpu_usage": float(self.cpu_usage) if self.cpu_usage is not None else None,
            "memory_usage": float(self.memory_usage) if self.memory_usage is not None else None,
            "disk_usage": float(self.disk_usage) if self.disk_usage is not None else None,
            "event_log_errors": self.event_log_errors,
        }


class AgentHeartbeat(Base):
    __tablename__ = "agent_heartbeat"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    agent_id = Column(String(64), nullable=False, index=True)
    ip_address = Column(String(45), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<AgentHeartbeat(agent_id={self.agent_id!r}, "
            f"ip={self.ip_address!r})>"
        )

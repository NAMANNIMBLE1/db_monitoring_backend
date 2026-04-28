from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, Numeric, String, UniqueConstraint

from models import Base


class LinuxPortMonitoring(Base):
    __tablename__ = "linux_port_monitoring"
    __table_args__ = (
        UniqueConstraint("ip_address", "timestamp", name="uq_linux_ip_timestamp"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ip_address = Column(String(45), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)

    # Service / process statuses: 'UP', 'DOWN', or NULL (agent offline)
    ssh_status = Column(String(16))
    systemd_status = Column(String(16))
    network_status = Column(String(16))
    ntp_status = Column(String(16))
    logging_status = Column(String(16))
    cron_status = Column(String(16))

    # System metrics
    cpu_usage = Column(Numeric(5, 2))
    memory_usage = Column(Numeric(5, 2))
    disk_usage = Column(Numeric(5, 2))
    load_average = Column(Numeric(7, 2))
    uptime = Column(Integer)

    def __repr__(self) -> str:
        return (
            f"<LinuxPortMonitoring(ip={self.ip_address!r}, "
            f"timestamp={self.timestamp!r})>"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "ssh_status": self.ssh_status,
            "systemd_status": self.systemd_status,
            "network_status": self.network_status,
            "ntp_status": self.ntp_status,
            "logging_status": self.logging_status,
            "cron_status": self.cron_status,
            "cpu_usage": float(self.cpu_usage) if self.cpu_usage is not None else None,
            "memory_usage": float(self.memory_usage) if self.memory_usage is not None else None,
            "disk_usage": float(self.disk_usage) if self.disk_usage is not None else None,
            "load_average": float(self.load_average) if self.load_average is not None else None,
            "uptime": self.uptime,
        }

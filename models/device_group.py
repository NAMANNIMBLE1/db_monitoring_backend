from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import relationship
from models import Base

class DeviceGroup(Base):
    __tablename__ = "device_group"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    dynamic_query = Column(Text, nullable=True)  
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    members = relationship("DeviceGroupMember", back_populates="group", cascade="all, delete-orphan")

class DeviceGroupMember(Base):
    __tablename__ = "device_group_members"
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("device_group.id", ondelete="CASCADE"), nullable=False)
    device_id = Column(Integer, ForeignKey("monitored_device.id", ondelete="CASCADE"), nullable=False)
    group = relationship("DeviceGroup", back_populates="members")

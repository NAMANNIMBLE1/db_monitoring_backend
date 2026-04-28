from sqlalchemy import Column, String

from models import Base


class SystemSetting(Base):
    __tablename__ = "system_setting"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=False)

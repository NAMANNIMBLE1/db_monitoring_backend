from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from models.registered_agent import RegisteredAgent  # noqa: E402, F401
from models.monitoring import WindowsPortMonitoring, AgentHeartbeat  # noqa: E402, F401
from models.linux_monitoring import LinuxPortMonitoring  # noqa: E402, F401
from models.device import MonitoredDevice  # noqa: E402, F401
from models.system_setting import SystemSetting  # noqa: E402, F401
from models.service_definition import ServiceDefinition  # noqa: E402, F401
from models.unified_monitoring import UnifiedMonitoring  # noqa: E402, F401
from models.db_monitoring import DbMonitoring, DbMonitoringAlert  # noqa: E402, F401
from models.db_schema_history import DbSchemaHistory  # noqa: E402, F401
from models.agent_service_override import AgentServiceOverride  # noqa: E402, F401
from models.service_pack import ServicePack, ServicePackItem  # noqa: E402, F401
from models.alert_threshold import AlertThreshold  # noqa: E402, F401
from models.device_group import DeviceGroup, DeviceGroupMember  # noqa: E402, F401

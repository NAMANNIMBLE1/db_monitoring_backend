"""Seed default service definitions on startup (idempotent).

Inserts rows only when the ``key`` does not already exist, so re-running
the seeder after a schema upgrade or application restart is safe.
"""

import json
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.service_definition import ServiceDefinition
from models.service_pack import ServicePack, ServicePackItem

logger = logging.getLogger(__name__)

# fmt: off
DEFAULT_SERVICES = [
    # ── Windows-only ────────────────────────────────────────────
    {
        "key": "winrm",       "display_name": "WinRM",          "category": "Network Services",
        "os_type": "windows", "check_type": "tcp",
        "tcp_ports": [5985, 5986], "win_service_names": ["WinRM"],
    },
    {
        "key": "rdp",         "display_name": "RDP",            "category": "Network Services",
        "os_type": "windows", "check_type": "tcp",
        "tcp_ports": [3389],  "win_service_names": ["TermService"],
    },
    {
        "key": "dns",         "display_name": "DNS Server",     "category": "Network Services",
        "os_type": "windows", "check_type": "udp_service",
        "tcp_ports": [53], "udp_ports": [53], "win_service_names": ["DNS"],
    },
    {
        "key": "dhcp",        "display_name": "DHCP Server",    "category": "Network Services",
        "os_type": "windows", "check_type": "udp_service",
        "udp_ports": [67, 68], "win_service_names": ["DHCPServer"],
    },
    {
        "key": "ad_ds",       "display_name": "Active Directory DS", "category": "Directory & Auth",
        "os_type": "windows", "check_type": "tcp_service",
        "tcp_ports": [389, 636, 88],  # LDAP, LDAPS, Kerberos — NO port 445 (SMB false positive)
        "win_service_names": ["NTDS"],
    },
    {
        "key": "w32time",     "display_name": "Windows Time",   "category": "Directory & Auth",
        "os_type": "windows", "check_type": "udp_service",
        "udp_ports": [123], "win_service_names": ["W32Time"],
    },
    {
        "key": "smb",         "display_name": "SMB / File Sharing", "category": "Network Services",
        "os_type": "windows", "check_type": "tcp_service",
        "tcp_ports": [445, 139], "win_service_names": ["LanmanServer"],
    },
    {
        "key": "iis",         "display_name": "IIS Web Server",  "category": "Web & Security",
        "os_type": "windows", "check_type": "tcp_service",
        "tcp_ports": [80, 443], "win_service_names": ["W3SVC"],
    },
    {
        "key": "defender",    "display_name": "Windows Defender", "category": "Web & Security",
        "os_type": "windows", "check_type": "service",
        "win_service_names": ["WinDefend"],
    },
    {
        "key": "rpc",         "display_name": "RPC",             "category": "Network Services",
        "os_type": "windows", "check_type": "service",
        "win_service_names": ["RpcSs"],
    },
    {
        "key": "sam",         "display_name": "SAM",             "category": "Directory & Auth",
        "os_type": "windows", "check_type": "service",
        "win_service_names": ["SamSs"],
    },
    {
        "key": "lanmanserver","display_name": "LanmanServer",    "category": "Network Services",
        "os_type": "windows", "check_type": "service",
        "win_service_names": ["LanmanServer"],
    },

    # ── Linux-only ──────────────────────────────────────────────
    {
        "key": "ssh",         "display_name": "SSH",             "category": "Remote Access",
        "os_type": "linux",   "check_type": "tcp_service",
        "tcp_ports": [22],
        "linux_service_names": ["sshd", "ssh"], "linux_process_names": ["sshd"],
    },
    {
        "key": "systemd",     "display_name": "systemd",         "category": "System Core",
        "os_type": "linux",   "check_type": "service",
        "linux_service_names": ["systemd-logind"], "linux_process_names": ["systemd"],
    },
    {
        "key": "network",     "display_name": "Networking",      "category": "System Core",
        "os_type": "linux",   "check_type": "service",
        "linux_service_names": ["NetworkManager", "systemd-networkd", "networking"],
        "linux_process_names": ["NetworkManager"],
    },
    {
        "key": "ntp",         "display_name": "NTP / Time Sync", "category": "System Core",
        "os_type": "linux",   "check_type": "udp_service",
        "udp_ports": [123],
        "linux_service_names": ["chronyd", "ntpd", "systemd-timesyncd"],
        "linux_process_names": ["chronyd"],
    },
    {
        "key": "logging",     "display_name": "Logging",         "category": "Services",
        "os_type": "linux",   "check_type": "service",
        "linux_service_names": ["rsyslog", "syslog-ng", "systemd-journald"],
        "linux_process_names": ["rsyslogd"],
    },
    {
        "key": "cron",        "display_name": "Cron",            "category": "Services",
        "os_type": "linux",   "check_type": "service",
        "linux_service_names": ["cron", "crond"], "linux_process_names": ["cron"],
    },

    # ── Both OS ─────────────────────────────────────────────────
    {
        "key": "nginx",       "display_name": "Nginx Web Server","category": "Web Server",
        "os_type": "both",    "check_type": "tcp_service",
        "tcp_ports": [80, 443],
        "win_service_names": ["nginx"],
        "linux_service_names": ["nginx"], "linux_process_names": ["nginx"],
    },
    {
        "key": "apache",      "display_name": "Apache HTTP Server","category": "Web Server",
        "os_type": "both",    "check_type": "tcp_service",
        "tcp_ports": [80, 443],
        "win_service_names": ["Apache2.4"],
        "linux_service_names": ["httpd", "apache2"], "linux_process_names": ["httpd", "apache2"],
    },
    {
        "key": "mysql",       "display_name": "MySQL",           "category": "Database",
        "os_type": "both",    "check_type": "tcp_service",
        "tcp_ports": [3306],
        "win_service_names": ["MySQL", "MySQL80"],
        "linux_service_names": ["mysql", "mysqld", "mariadb"], "linux_process_names": ["mysqld"],
    },
    {
        "key": "postgresql",  "display_name": "PostgreSQL",      "category": "Database",
        "os_type": "both",    "check_type": "tcp_service",
        "tcp_ports": [5432],
        "win_service_names": ["postgresql-x64-14", "postgresql-x64-15", "postgresql-x64-16"],
        "linux_service_names": ["postgresql"], "linux_process_names": ["postgres"],
    },
    {
        "key": "mongodb",     "display_name": "MongoDB",         "category": "Database",
        "os_type": "both",    "check_type": "tcp_service",
        "tcp_ports": [27017],
        "win_service_names": ["MongoDB"],
        "linux_service_names": ["mongod"], "linux_process_names": ["mongod"],
    },
    {
        "key": "snmp",        "display_name": "SNMP",            "category": "Network Services",
        "os_type": "both",    "check_type": "udp_service",
        "udp_ports": [161],
        "win_service_names": ["SNMP"],
        "linux_service_names": ["snmpd"], "linux_process_names": ["snmpd"],
    },
    {
        "key": "docker",      "display_name": "Docker",          "category": "Containers",
        "os_type": "both",    "check_type": "tcp_service",
        "tcp_ports": [2375, 2376],
        "win_service_names": ["com.docker.service", "docker"],
        "linux_service_names": ["docker"], "linux_process_names": ["dockerd"],
    },
    {
        "key": "ftp",         "display_name": "FTP",             "category": "File Transfer",
        "os_type": "both",    "check_type": "tcp",
        "tcp_ports": [21],
        "win_service_names": ["ftpsvc"],
        "linux_service_names": ["vsftpd", "proftpd"], "linux_process_names": ["vsftpd"],
    },
    {
        "key": "sftp",        "display_name": "SFTP",            "category": "File Transfer",
        "os_type": "both",    "check_type": "tcp",
        "tcp_ports": [22],
    },
    {
        "key": "smtp",        "display_name": "SMTP",            "category": "Mail",
        "os_type": "both",    "check_type": "tcp",
        "tcp_ports": [25, 587],
        "win_service_names": ["SMTPSVC"],
        "linux_service_names": ["postfix", "sendmail", "exim4"], "linux_process_names": ["master"],
    },
    {
        "key": "redis",       "display_name": "Redis",           "category": "Database",
        "os_type": "both",    "check_type": "tcp_service",
        "tcp_ports": [6379],
        "win_service_names": ["Redis"],
        "linux_service_names": ["redis", "redis-server"], "linux_process_names": ["redis-server"],
    },
    {
        "key": "elasticsearch","display_name": "Elasticsearch",  "category": "Database",
        "os_type": "both",    "check_type": "tcp_service",
        "tcp_ports": [9200, 9300],
        "win_service_names": ["elasticsearch-service-x64"],
        "linux_service_names": ["elasticsearch"], "linux_process_names": ["java"],
    },
]
# fmt: on


def _jsonify(val) -> str | None:
    """Serialise a Python list to a JSON string, or return None for empty/absent."""
    if not val:
        return None
    return json.dumps(val)


async def seed_default_services(db: AsyncSession) -> int:
    """Insert default service definitions that don't already exist.

    Returns the count of newly inserted rows.
    """
    # Fetch existing keys in one query
    result = await db.execute(select(ServiceDefinition.key))
    existing_keys = {row[0] for row in result.fetchall()}

    inserted = 0
    for svc in DEFAULT_SERVICES:
        if svc["key"] in existing_keys:
            continue

        row = ServiceDefinition(
            key=svc["key"],
            display_name=svc["display_name"],
            category=svc["category"],
            os_type=svc["os_type"],
            check_type=svc["check_type"],
            tcp_ports=_jsonify(svc.get("tcp_ports")),
            udp_ports=_jsonify(svc.get("udp_ports")),
            win_service_names=_jsonify(svc.get("win_service_names")),
            linux_service_names=_jsonify(svc.get("linux_service_names")),
            linux_process_names=_jsonify(svc.get("linux_process_names")),
            is_default=True,
            is_active=True,
        )
        db.add(row)
        inserted += 1

    if inserted:
        await db.commit()
        logger.info("Seeded %d new default service definitions", inserted)
    else:
        logger.debug("All default service definitions already exist")

    return inserted


async def ensure_agent_pack_column(db: AsyncSession) -> None:
    """Add service_pack_id column to registered_agent if it doesn't exist.

    SQLAlchemy create_all won't add columns to existing tables,
    so we do it manually with ALTER TABLE.
    """
    try:
        await db.execute(text(
            "ALTER TABLE registered_agent ADD COLUMN service_pack_id INT NULL"
        ))
        await db.commit()
        logger.info("Added service_pack_id column to registered_agent")
    except Exception:
        await db.rollback()
        # Column already exists — expected on subsequent startups
        logger.debug("service_pack_id column already exists (or table doesn't exist yet)")


async def seed_master_service_pack(db: AsyncSession) -> None:
    """Create or sync the Master Service Pack.

    The Master Pack contains all default active services. It's
    auto-assigned to new agents and cannot be deleted or modified.
    """
    # Check if master pack exists
    result = await db.execute(
        select(ServicePack).where(ServicePack.is_master == True)
    )
    master = result.scalars().first()

    if not master:
        master = ServicePack(name="Master Service Pack", is_master=True)
        db.add(master)
        await db.flush()
        logger.info("Created Master Service Pack (id=%d)", master.id)

    # Get all default active service keys
    result = await db.execute(
        select(ServiceDefinition.key).where(
            ServiceDefinition.is_default == True,
            ServiceDefinition.is_active == True,
        )
    )
    default_keys = {row[0] for row in result.fetchall()}

    # Get current pack items
    result = await db.execute(
        select(ServicePackItem.service_key).where(ServicePackItem.pack_id == master.id)
    )
    current_keys = {row[0] for row in result.fetchall()}

    # Add missing
    added = 0
    for key in default_keys - current_keys:
        db.add(ServicePackItem(pack_id=master.id, service_key=key))
        added += 1

    # Remove stale (services that were removed from defaults)
    removed = 0
    for key in current_keys - default_keys:
        await db.execute(
            text("DELETE FROM service_pack_item WHERE pack_id = :pid AND service_key = :key"),
            {"pid": master.id, "key": key},
        )
        removed += 1

    if added or removed:
        await db.commit()
        logger.info("Master Service Pack synced: +%d/-%d services (total: %d)", added, removed, len(default_keys))
    else:
        logger.debug("Master Service Pack up to date (%d services)", len(current_keys))

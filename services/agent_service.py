import hashlib
import logging
import secrets
from utils.timezone import now_ist
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.registered_agent import RegisteredAgent

logger = logging.getLogger(__name__)


def _hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a raw token string."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def register_agent(
    db: AsyncSession,
    agent_id: str,
    ip_address: str,
    hostname: str,
    os_type: str,
    agent_version: str,
    master_key: str,
    expected_master_key: str,
) -> dict:
    """
    Register a new agent or refresh an existing agent's token.

    Returns a dict with status, auth_token, and agent_id on success.
    Raises ValueError if master_key is invalid.
    """
    if master_key != expected_master_key:
        raise ValueError("Invalid master key")

    raw_token = secrets.token_hex(64)
    token_hash = _hash_token(raw_token)

    # Look up by agent_id first, then fall back to ip_address.
    # An agent may generate a new UUID (reinstall, data dir change) but
    # the same IP should reuse the existing registration row.
    result = await db.execute(
        select(RegisteredAgent).where(RegisteredAgent.agent_id == agent_id)
    )
    existing = result.scalars().first()

    if existing is None:
        # Check by IP -- same machine, new agent_id
        result = await db.execute(
            select(RegisteredAgent).where(RegisteredAgent.ip_address == ip_address)
        )
        existing = result.scalars().first()

    if existing is None:
        # Assign Master Service Pack to new agents
        master_pack_id = None
        try:
            from models.service_pack import ServicePack
            master_result = await db.execute(
                select(ServicePack).where(ServicePack.is_master == True)
            )
            master_pack = master_result.scalars().first()
            if master_pack:
                master_pack_id = master_pack.id
        except Exception:
            pass  # Table may not exist yet on first boot

        new_agent = RegisteredAgent(
            agent_id=agent_id,
            ip_address=ip_address,
            hostname=hostname,
            os_type=os_type,
            agent_version=agent_version,
            registered_at=now_ist(),
            last_seen=now_ist(),
            auth_token_hash=token_hash,
            service_pack_id=master_pack_id,
        )
        db.add(new_agent)
        await db.commit()
        logger.info("New agent registered: %s (%s) with pack_id=%s", agent_id, ip_address, master_pack_id)
        return {
            "status": "registered",
            "auth_token": raw_token,
            "agent_id": agent_id,
        }
    else:
        existing.agent_id = agent_id
        existing.last_seen = now_ist()
        existing.auth_token_hash = token_hash
        existing.hostname = hostname
        existing.os_type = os_type
        existing.agent_version = agent_version
        existing.ip_address = ip_address
        await db.commit()
        logger.info("Existing agent re-registered: %s (%s)", agent_id, ip_address)
        return {
            "status": "updated",
            "auth_token": raw_token,
            "agent_id": agent_id,
        }


async def validate_token(
    db: AsyncSession,
    token: str,
    ip_address: str,
    agent_id: str,
) -> Optional[RegisteredAgent]:
    """
    Validate a Bearer token against stored hashes.

    Returns the RegisteredAgent row if the token, ip_address, and agent_id all
    match; otherwise returns None.
    """
    token_hash = _hash_token(token)
    result = await db.execute(
        select(RegisteredAgent).where(
            RegisteredAgent.auth_token_hash == token_hash,
            RegisteredAgent.ip_address == ip_address,
            RegisteredAgent.agent_id == agent_id,
        )
    )
    agent = result.scalars().first()
    return agent


async def set_agent_blocked(db: AsyncSession, agent_id: str, blocked: bool) -> bool:
    """Set is_blocked flag. Returns True if agent found, False otherwise."""
    result = await db.execute(
        select(RegisteredAgent).where(RegisteredAgent.agent_id == agent_id)
    )
    agent = result.scalars().first()
    if not agent:
        return False
    agent.is_blocked = blocked
    await db.commit()
    return True


async def update_last_seen(db: AsyncSession, agent_id: str) -> None:
    """Update the last_seen timestamp for a given agent."""
    await db.execute(
        update(RegisteredAgent)
        .where(RegisteredAgent.agent_id == agent_id)
        .values(last_seen=now_ist())
    )
    await db.commit()
    logger.debug("Updated last_seen for agent %s", agent_id)

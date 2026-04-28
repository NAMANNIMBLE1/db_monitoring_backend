import json
import logging
import time
from collections import defaultdict
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from services.agent_service import validate_token

logger = logging.getLogger(__name__)

# ── In-memory rate limiter state ──
_rate_store: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60  # per 60 seconds


def _extract_token(authorization: Optional[str]) -> str:
    """Extract the raw token from a 'Bearer <token>' header value."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Authorization header must be 'Bearer <token>'",
        )
    return parts[1]


async def get_current_agent(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    FastAPI dependency that validates the Bearer token against the database.

    Expects the request body to contain 'agent_id' and 'ip_address' fields
    (already parsed by the route handler and stashed in request.state).

    Returns the RegisteredAgent ORM object on success; raises 401 otherwise.
    """
    token = _extract_token(authorization)

    agent_id: Optional[str] = getattr(request.state, "agent_id", None)
    ip_address: Optional[str] = getattr(request.state, "ip_address", None)

    if not agent_id or not ip_address:
        raise HTTPException(
            status_code=400,
            detail="agent_id and ip_address are required in the request body",
        )

    agent = await validate_token(db, token, ip_address, agent_id)
    if agent is None:
        logger.warning(
            "Auth failed for agent_id=%s ip=%s", agent_id, ip_address
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    return agent


async def _extract_rate_key(request: Request) -> str:
    """
    Determine the rate-limit key for this request.

    For heartbeat endpoints the JSON body contains ``ip_address`` which
    uniquely identifies the agent.  Using it as the key means each agent
    gets its own rate-limit bucket — correct both in production (one
    agent per machine) and during load-testing (many simulated agents
    from one machine).

    Falls back to the TCP client IP for non-JSON requests or endpoints
    that don't carry ``ip_address`` (e.g. ``/register``).
    """
    try:
        body_bytes = await request.body()        # cached by Starlette
        if body_bytes:
            data = json.loads(body_bytes)
            if isinstance(data, dict) and data.get("ip_address"):
                return data["ip_address"]
            # batch endpoints send a list — use the first entry
            if isinstance(data, list) and data and data[0].get("ip_address"):
                return data[0]["ip_address"]
    except Exception:
        pass
    return request.client.host if request.client else "unknown"


async def rate_limit(request: Request) -> None:
    """
    In-memory rate limiter: max N requests per minute per agent IP.

    The limit is read from ``settings.RATE_LIMIT_RPM`` (default 30).
    Set ``RATE_LIMIT_RPM=0`` in ``.env`` to disable rate limiting.
    """
    limit = settings.RATE_LIMIT_RPM
    if limit <= 0:
        return  # rate limiting disabled

    key = await _extract_rate_key(request)
    now = time.time()
    window_start = now - _RATE_WINDOW

    # Prune expired timestamps
    _rate_store[key] = [
        ts for ts in _rate_store[key] if ts > window_start
    ]

    if len(_rate_store[key]) >= limit:
        logger.warning("Rate limit exceeded for %s", key)
        raise HTTPException(status_code=429, detail="Too many requests")

    _rate_store[key].append(now)

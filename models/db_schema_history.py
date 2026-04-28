"""Schema version history for DDL audit tracking.

Stores a record ONLY when a schema change is detected — not every cycle.
Each record contains the full schema snapshot + a diff of what changed.
"""

import json
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT

from models import Base


class DbSchemaHistory(Base):
    __tablename__ = "db_schema_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ip_address = Column(String(45), nullable=False, index=True)
    instance_name = Column(String(128), nullable=False, index=True)
    db_type = Column(String(20), nullable=False)
    version = Column(Integer, nullable=False)  # incrementing version number per instance
    schema_hash = Column(String(64), nullable=False)  # SHA-256 of the schema snapshot
    snapshot = Column(LONGTEXT, nullable=False)  # full schema JSON (can be large)
    changes = Column(LONGTEXT)  # JSON diff: what changed from previous version
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return (
            f"<DbSchemaHistory(ip={self.ip_address!r}, instance={self.instance_name!r}, "
            f"v={self.version})>"
        )

    def to_dict(self) -> dict:
        snapshot_parsed = None
        if self.snapshot:
            try:
                snapshot_parsed = json.loads(self.snapshot)
            except (json.JSONDecodeError, ValueError):
                snapshot_parsed = {}

        changes_parsed = None
        if self.changes:
            try:
                changes_parsed = json.loads(self.changes)
            except (json.JSONDecodeError, ValueError):
                changes_parsed = {}

        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "instance_name": self.instance_name,
            "db_type": self.db_type,
            "version": self.version,
            "schema_hash": self.schema_hash,
            "snapshot": snapshot_parsed,
            "changes": changes_parsed,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
        }

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLogEntry


async def record_event(session: AsyncSession, event_type: str, details: dict) -> None:
    session.add(AuditLogEntry(event_type=event_type, details=details))
    await session.commit()

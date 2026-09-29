from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, echo=False)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_models(engine: AsyncEngine) -> None:
    # No migration tool (Alembic) for the hackathon timeline — create_all is
    # enough for an append-only schema that hasn't shipped yet. Revisit if a
    # real migration is ever needed post-submission.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

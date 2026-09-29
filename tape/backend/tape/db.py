"""Baza danych: fill-e są niemutowalnym źródłem prawdy, pozycje liczymy z nich na żądanie.

DATABASE_URL: domyślnie SQLite (dev); w produkcji PostgreSQL (postgresql+psycopg://…).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, List, Tuple

from sqlalchemy import (JSON, DateTime, Integer, Numeric, String, TypeDecorator, UniqueConstraint,
                        create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .importers.base import Fill


class ExactDecimal(TypeDecorator):
    """NUMERIC w Postgresie, tekst w SQLite — bez utraty precyzji przez float."""

    impl = String(64)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(28, 10))
        return dialect.type_descriptor(String(64))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(str(value))


class UtcDateTime(TypeDecorator):
    """SQLite gubi strefę czasową — zapisujemy UTC i przywracamy tzinfo przy odczycie."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.astimezone(timezone.utc) if value is not None else None

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


class FillRow(Base):
    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("account", "source", "external_id", name="uq_fill_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String(64), default="default", index=True)
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    ts: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[Decimal] = mapped_column(ExactDecimal)
    price: Mapped[Decimal] = mapped_column(ExactDecimal)
    contract_size: Mapped[Decimal] = mapped_column(ExactDecimal)
    fee: Mapped[Decimal] = mapped_column(ExactDecimal)
    broker_pnl: Mapped[Decimal | None] = mapped_column(ExactDecimal, nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(ExactDecimal, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")

    def to_fill(self) -> Fill:
        return Fill(external_id=f"{self.source}:{self.external_id}", ts=self.ts, symbol=self.symbol,
                    side=self.side, qty=self.qty, price=self.price, contract_size=self.contract_size,
                    fee=self.fee, broker_pnl=self.broker_pnl, stop_loss=self.stop_loss, currency=self.currency)


class ImportRow(Base):
    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String(64), default="default")
    source: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=lambda: datetime.now(timezone.utc))
    new: Mapped[int] = mapped_column(Integer)
    duplicates: Mapped[int] = mapped_column(Integer)
    errors: Mapped[list] = mapped_column(JSON, default=list)


def make_sessionmaker(url: str | None = None) -> sessionmaker:
    url = url or os.getenv("DATABASE_URL", "sqlite:///tape.db")
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    engine = create_engine(url, **kwargs)
    from . import journal  # noqa: F401 — rejestruje tabele journala
    from .news import store  # noqa: F401 — rejestruje tabele newsów w metadanych
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def store_fills(session: Session, account: str, source: str, fills: Iterable[Fill]) -> Tuple[int, int]:
    """Zapisz fill-e idempotentnie; zwraca (nowe, duplikaty)."""
    fills = list(fills)
    existing = set(session.scalars(
        select(FillRow.external_id).where(FillRow.account == account, FillRow.source == source)
    ))
    new = 0
    seen_now = set()
    for f in fills:
        if f.external_id in existing or f.external_id in seen_now:
            continue
        seen_now.add(f.external_id)
        session.add(FillRow(account=account, source=source, external_id=f.external_id, ts=f.ts,
                            symbol=f.symbol, side=f.side, qty=f.qty, price=f.price,
                            contract_size=f.contract_size, fee=f.fee, broker_pnl=f.broker_pnl,
                            stop_loss=f.stop_loss, currency=f.currency))
        new += 1
    return new, len(fills) - new


def load_fills(session: Session, account: str = "default") -> List[Fill]:
    rows = session.scalars(select(FillRow).where(FillRow.account == account).order_by(FillRow.ts))
    return [r.to_fill() for r in rows]

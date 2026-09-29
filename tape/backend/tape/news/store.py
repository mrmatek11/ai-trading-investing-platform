"""Trwałe dane pipeline'u newsów. Oceny nastawienia są APPEND-ONLY — to one tworzą track record."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import JSON, Float, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..db import Base, UtcDateTime
from .bias import Event, Impact
from .classify import Article


class ArticleRow(Base):
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True)
    title: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    cluster_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)

    def to_article(self) -> Article:
        return Article(title=self.title, text=self.title, url=self.url, published_at=self.published_at)


class EventRow(Base):
    __tablename__ = "news_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)          # = id klastra
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=lambda: datetime.now(timezone.utc))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(16))
    place: Mapped[str] = mapped_column(String(128))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    impacts: Mapped[dict] = mapped_column(JSON)
    novelty: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(Text)
    sources: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(64))

    def to_event(self) -> Event:
        return Event(
            id=self.id, title=self.title, category=self.category, lat=self.lat, lon=self.lon,
            occurred_at=self.occurred_at, novelty=self.novelty, confidence=self.confidence,
            place=self.place, summary=self.summary, sources=self.sources,
            impacts={k: Impact(v["direction"], v["magnitude"], v["horizon"]) for k, v in self.impacts.items()},
        )


class PriceRow(Base):
    """Ceny do liczenia trafności (np. XAU spot co godzinę)."""

    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("asset", "ts", name="uq_price"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset: Mapped[str] = mapped_column(String(8), index=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    price: Mapped[float] = mapped_column(Float)


class BiasSnapshot(Base):
    """Jedna ocena nastawienia zapisana w chwili `ts` — zanim znany jest wynik rynku."""

    __tablename__ = "bias_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    asset: Mapped[str] = mapped_column(String(8), index=True)
    score: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(8))
    events_used: Mapped[int] = mapped_column(Integer)


def save_event(session: Session, e: Event, model: str) -> None:
    session.merge(EventRow(
        id=e.id, occurred_at=e.occurred_at, title=e.title, category=e.category, place=e.place,
        lat=e.lat, lon=e.lon, novelty=e.novelty, confidence=e.confidence, summary=e.summary,
        sources=e.sources, model=model,
        impacts={k: {"direction": v.direction, "magnitude": v.magnitude, "horizon": v.horizon} for k, v in e.impacts.items()},
    ))


def recent_events(session: Session, now: datetime, days: int = 14) -> List[Event]:
    rows = session.scalars(select(EventRow).where(EventRow.occurred_at >= now - timedelta(days=days)))
    return [r.to_event() for r in rows]


def price_series(session: Session, asset: str) -> List[tuple]:
    return [(r.ts, r.price) for r in session.scalars(select(PriceRow).where(PriceRow.asset == asset).order_by(PriceRow.ts))]


def add_prices(session: Session, asset: str, rows) -> int:
    """Dopisz ceny (ts, price); istniejące znaczniki czasu są pomijane."""
    existing = set(session.scalars(select(PriceRow.ts).where(PriceRow.asset == asset)))
    n = 0
    for ts, price in rows:
        if ts not in existing:
            session.add(PriceRow(asset=asset, ts=ts, price=float(price)))
            existing.add(ts)
            n += 1
    return n


def snapshots(session: Session, asset: str) -> List[BiasSnapshot]:
    return list(session.scalars(select(BiasSnapshot).where(BiasSnapshot.asset == asset).order_by(BiasSnapshot.ts)))

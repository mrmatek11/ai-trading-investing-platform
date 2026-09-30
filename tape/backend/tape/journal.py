"""Journal: playbooki (setupy z regułami), wpisy do transakcji i taksonomia błędów.

Wpis jest przypięty do stabilnego klucza pozycji (Position.key). Ręczny stop loss z wpisu
uzupełnia pozycje, dla których broker nie podał SL (np. MT5), więc R liczy się też dla nich.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, ExactDecimal, UtcDateTime
from .engine.positions import Position

# Najczęstsze błędy z researchu (docs/RESEARCH.md) — użytkownik może dodawać własne.
MISTAKES = [
    "FOMO — wejście za późno",
    "Wejście po stracie (revenge)",
    "Przesunięty stop loss",
    "Za duża pozycja",
    "Wejście przed newsem",
    "Brak setupu / planu",
    "Za wczesne wyjście",
    "Złamana reguła prop firmy",
]


class Setup(Base):
    __tablename__ = "setups"
    __table_args__ = (UniqueConstraint("account", "name", name="uq_setup_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String(64), default="default", index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    rules: Mapped[list] = mapped_column(JSON, default=list)          # lista reguł-checklisty
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=lambda: datetime.now(timezone.utc))


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    __table_args__ = (UniqueConstraint("account", "position_key", name="uq_journal_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String(64), default="default", index=True)
    position_key: Mapped[str] = mapped_column(String(32), index=True)
    setup_id: Mapped[Optional[int]] = mapped_column(ForeignKey("setups.id", ondelete="SET NULL"), nullable=True)
    checklist: Mapped[dict] = mapped_column(JSON, default=dict)      # reguła → spełniona?
    mistakes: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    initial_stop: Mapped[Optional[Decimal]] = mapped_column(ExactDecimal, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=lambda: datetime.now(timezone.utc))


def entries_by_key(session: Session, account: str) -> Dict[str, JournalEntry]:
    return {e.position_key: e for e in session.scalars(select(JournalEntry).where(JournalEntry.account == account))}


def apply_manual_stops(positions: Sequence[Position], entries: Dict[str, JournalEntry]) -> None:
    """Ręczny SL z journala wypełnia brak SL od brokera (nie nadpisuje SL z pliku)."""
    for p in positions:
        e = entries.get(p.key)
        if e is not None and e.initial_stop is not None and p.initial_stop is None:
            p.initial_stop = e.initial_stop


@dataclass
class GroupStats:
    key: str
    trades: int
    net_pnl: float
    win_rate: float
    avg_pnl: float
    avg_r: Optional[float]


def _group(items: List[Position], key: str) -> GroupStats:
    pnls = [float(p.net_pnl) for p in items]
    rs = [float(p.r_multiple) for p in items if p.r_multiple is not None]
    return GroupStats(key=key, trades=len(items), net_pnl=round(sum(pnls), 2),
                      win_rate=sum(1 for x in pnls if x > 0) / len(pnls), avg_pnl=round(sum(pnls) / len(pnls), 2),
                      avg_r=round(sum(rs) / len(rs), 3) if rs else None)


def setup_stats(positions: Sequence[Position], entries: Dict[str, JournalEntry], setups: Dict[int, Setup]) -> List[GroupStats]:
    groups: Dict[str, List[Position]] = defaultdict(list)
    for p in positions:
        if p.is_open:
            continue
        e = entries.get(p.key)
        name = setups[e.setup_id].name if e and e.setup_id in setups else "Bez setupu"
        groups[name].append(p)
    return sorted((_group(v, k) for k, v in groups.items()), key=lambda g: -g.trades)


def mistake_costs(positions: Sequence[Position], entries: Dict[str, JournalEntry]) -> List[GroupStats]:
    """Wynik transakcji oznaczonych danym błędem — ile ten błąd Cię kosztował."""
    groups: Dict[str, List[Position]] = defaultdict(list)
    for p in positions:
        e = entries.get(p.key)
        if p.is_open or e is None:
            continue
        for m in e.mistakes or []:
            groups[m].append(p)
    return sorted((_group(v, k) for k, v in groups.items()), key=lambda g: g.net_pnl)

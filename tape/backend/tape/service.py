"""Wspólne operacje na danych użytkownika — używane przez API i workery (raporty, alerty)."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy.orm import Session

from . import journal
from .db import load_fills_by_book
from .engine.positions import Position, build_positions


def load_positions(session: Session, account: str, book: Optional[str] = None) -> List[Position]:
    """Pozycje liczone osobno dla każdego rachunku — long na jednym koncie nie zamyka shorta na drugim."""
    items: List[Position] = []
    for b, fills in load_fills_by_book(session, account, book or None).items():
        for p in build_positions(fills):
            p.book = b
            items.append(p)
    items.sort(key=lambda p: p.opened_at)
    journal.apply_manual_stops(items, journal.entries_by_key(session, account))
    return items

"""Kalendarz ekonomiczny: ważne dane makro (Fed, CPI, NFP…) i transakcje zawarte w ich pobliżu.

Źródła (wspólne dla wszystkich użytkowników):
- seed — daty decyzji FOMC wpisane w kodzie (godzina 14:00 czasu Nowego Jorku). ⚠️ Zweryfikować
  z harmonogramem na federalreserve.gov.
- forexfactory — nieoficjalny tygodniowy JSON (TAPE_CALENDAR_FEED=forexfactory). Do testów; przed
  produkcją sprawdzić warunki użycia albo wybrać licencjonowanego dostawcę.
- csv — wgranie przez administratora (kolumny: time [UTC], country, title, impact).

Worker:  python -m tape.econ_calendar --every 21600
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
import urllib.request
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, UtcDateTime

log = logging.getLogger("tape.econ_calendar")

WINDOW = timedelta(minutes=30)
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
IMPACTS = ("high", "medium", "low")
# Decyzje FOMC 2026 (drugi dzień posiedzenia). ⚠️ zweryfikować na federalreserve.gov
FOMC_2026 = [date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
             date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9)]


class EconEventRow(Base):
    __tablename__ = "econ_events"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_econ_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    country: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(120))
    impact: Mapped[str] = mapped_column(String(8))
    forecast: Mapped[str] = mapped_column(String(32), default="")
    previous: Mapped[str] = mapped_column(String(32), default="")


def _eid(ts: datetime, country: str, title: str) -> str:
    return hashlib.sha1(f"{ts.isoformat()}|{country}|{title}".encode()).hexdigest()[:16]


def fomc_seed() -> List[Dict[str, object]]:
    ny = ZoneInfo("America/New_York")
    out = []
    for d in FOMC_2026:
        ts = datetime(d.year, d.month, d.day, 14, 0, tzinfo=ny).astimezone(timezone.utc)
        out.append({"ts": ts, "country": "USD", "title": "Decyzja FOMC (stopy procentowe)", "impact": "high"})
    return out


def parse_forexfactory(data: bytes) -> List[Dict[str, object]]:
    items = json.loads(data)
    if not isinstance(items, list):
        raise ValueError("oczekiwano listy zdarzeń")
    out = []
    for x in items:
        impact = str(x.get("impact", "")).strip().lower()
        if impact not in IMPACTS:
            continue                                   # „Holiday”, „Non-Economic”
        ts = datetime.fromisoformat(str(x["date"])).astimezone(timezone.utc)
        out.append({"ts": ts, "country": str(x.get("country", ""))[:8], "title": str(x.get("title", ""))[:120],
                    "impact": impact, "forecast": str(x.get("forecast") or "")[:32],
                    "previous": str(x.get("previous") or "")[:32]})
    return out


def parse_csv(data: bytes, filename: str = "calendar.csv") -> List[Dict[str, object]]:
    from .importers.base import pick, read_rows, require, to_utc

    out = []
    for r in read_rows(data, filename):
        impact = str(pick(r, "impact") or "high").strip().lower()
        if impact not in IMPACTS:
            raise ValueError(f"impact musi być jednym z {IMPACTS}: {impact!r}")
        out.append({"ts": to_utc(require(r, "time", "timestamp", "date"), "UTC"),
                    "country": str(pick(r, "country", "currency") or "USD")[:8],
                    "title": str(require(r, "title", "event"))[:120], "impact": impact})
    return out


def add_events(session: Session, source: str, events: Iterable[Dict[str, object]]) -> int:
    existing = set(session.scalars(select(EconEventRow.external_id).where(EconEventRow.source == source)))
    n = 0
    for e in events:
        eid = _eid(e["ts"], e["country"], e["title"])
        if eid in existing:
            continue
        existing.add(eid)
        session.add(EconEventRow(source=source, external_id=eid, ts=e["ts"], country=e["country"], title=e["title"],
                                 impact=e["impact"], forecast=e.get("forecast", ""), previous=e.get("previous", "")))
        n += 1
    return n


def ensure_seed(session: Session) -> None:
    if session.scalars(select(EconEventRow.id).where(EconEventRow.source == "seed").limit(1)).first() is None:
        add_events(session, "seed", fomc_seed())
        session.commit()


def relevant(session: Session, start: datetime, end: datetime, impact: str = "high") -> List[EconEventRow]:
    """Dane ważne dla metali: USD (Fed, inflacja, rynek pracy) o danym poziomie istotności."""
    levels = IMPACTS[: IMPACTS.index(impact) + 1]
    rows = session.scalars(select(EconEventRow).where(EconEventRow.ts >= start, EconEventRow.ts <= end,
                                                      EconEventRow.country == "USD", EconEventRow.impact.in_(levels))
                           .order_by(EconEventRow.ts))
    seen, out = set(), []
    for r in rows:                                     # to samo zdarzenie z dwóch źródeł pokazujemy raz
        key = (r.ts, r.title.lower()[:12])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def near(times: Sequence[datetime], ts: datetime, window: timedelta = WINDOW) -> bool:
    """Czy `ts` leży w ±window od któregoś zdarzenia (times posortowane)."""
    i = bisect_left(times, ts - window)
    return i < len(times) and times[i] <= ts + window


def to_dict(r: EconEventRow) -> Dict[str, object]:
    return {"ts": r.ts.isoformat(), "country": r.country, "title": r.title, "impact": r.impact,
            "forecast": r.forecast, "previous": r.previous, "source": r.source}


def _open(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tape/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — stały adres
        return resp.read()


def refresh(session: Session, opener: Callable[[str], bytes] = _open) -> Dict[str, object]:
    ensure_seed(session)
    feed = os.getenv("TAPE_CALENDAR_FEED", "").strip().lower()
    if feed != "forexfactory":
        return {"seed": True, "feed": None}
    added = add_events(session, "forexfactory", parse_forexfactory(opener(FF_URL)))
    session.commit()
    return {"seed": True, "feed": feed, "added": added}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Kalendarz ekonomiczny Tape")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=21600)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from .db import make_sessionmaker

    Session_ = make_sessionmaker()
    while True:
        with Session_() as s:
            try:
                log.info("kalendarz: %s", refresh(s))
            except Exception as exc:  # źródło chwilowo niedostępne — spróbujemy przy kolejnym przebiegu
                s.rollback()
                log.warning("kalendarz: %s", exc)
        if args.once:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()

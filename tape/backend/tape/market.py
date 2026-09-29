"""Ceny XAU / XAG od zewnętrznego dostawcy.

Worker zapisuje zamknięte świece H1 do tabeli `prices` (wykresy transakcji, trafność nastawienia newsów)
i ostatnią cenę do `quotes` (wycena portfela, pasek cen). Dostawcę wybiera TAPE_PRICE_PROVIDER:

- twelvedata — klucz TWELVEDATA_API_KEY; darmowy plan wystarcza do testów (2 symbole co kilka minut).
- oanda      — konto demo (fxTrade Practice): OANDA_TOKEN, OANDA_ENV=practice|live.
- goldapi    — api.gold-api.com, bez klucza; tylko bieżąca cena (bez historii). ⚠️ format odpowiedzi
               zweryfikować na żywo.

⚠️ Darmowe / demo plany służą do developmentu. Pokazywanie cen płacącym użytkownikom wymaga licencji
dostawcy na redystrybucję (display) — sprawdzić przed startem produkcyjnym.

Worker:  python -m tape.market --every 300
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from sqlalchemy import Float, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, UtcDateTime

log = logging.getLogger("tape.market")

ASSETS = ("XAU", "XAG")
HOUR = timedelta(hours=1)
Opener = Callable[[str, Dict[str, str]], bytes]


class QuoteRow(Base):
    __tablename__ = "quotes"

    asset: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime)
    price: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(16))


@dataclass
class Snapshot:
    candles: List[Tuple[datetime, float]]          # (czas zamknięcia świecy, close) — tylko zamknięte
    quote: Optional[Tuple[datetime, float]]


class ProviderError(RuntimeError):
    pass


def _default_opener(url: str, headers: Dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tape/0.1", **headers})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — stałe adresy dostawców
        return resp.read()


def _json(opener: Opener, url: str, headers: Optional[Dict[str, str]] = None):
    try:
        return json.loads(opener(url, headers or {}))
    except ValueError as exc:
        raise ProviderError(f"niepoprawna odpowiedź JSON z {urllib.parse.urlsplit(url).netloc}") from exc


class TwelveData:
    name = "twelvedata"
    base = "https://api.twelvedata.com"
    symbols = {"XAU": "XAU/USD", "XAG": "XAG/USD"}

    def __init__(self, api_key: str, opener: Opener = _default_opener):
        self.key, self.opener = api_key, opener

    def fetch(self, asset: str, now: datetime, bars: int = 48) -> Snapshot:
        q = urllib.parse.urlencode({"symbol": self.symbols[asset], "interval": "1h", "outputsize": bars,
                                    "timezone": "UTC", "apikey": self.key})
        data = _json(self.opener, f"{self.base}/time_series?{q}")
        if data.get("status") == "error":
            raise ProviderError(f"Twelve Data: {data.get('message', 'błąd')} (kod {data.get('code')})")
        candles, last = [], None
        for v in data.get("values", []):
            start = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            close = float(v["close"])
            if start + HOUR <= now:
                candles.append((start + HOUR, close))
            if last is None or start > last[0]:
                last = (start, close)
        candles.sort()
        return Snapshot(candles, (min(last[0] + HOUR, now), last[1]) if last else None)


class Oanda:
    name = "oanda"
    hosts = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}
    symbols = {"XAU": "XAU_USD", "XAG": "XAG_USD"}

    def __init__(self, token: str, env: str = "practice", opener: Opener = _default_opener):
        if env not in self.hosts:
            raise ValueError("OANDA_ENV: practice albo live")
        self.token, self.base, self.opener = token, self.hosts[env], opener

    def fetch(self, asset: str, now: datetime, bars: int = 48) -> Snapshot:
        q = urllib.parse.urlencode({"granularity": "H1", "count": bars, "price": "M"})
        data = _json(self.opener, f"{self.base}/v3/instruments/{self.symbols[asset]}/candles?{q}",
                     {"Authorization": f"Bearer {self.token}", "Accept-Datetime-Format": "RFC3339"})
        if "candles" not in data:
            raise ProviderError(f"OANDA: {data.get('errorMessage', 'brak świec w odpowiedzi')}")
        candles, last = [], None
        for c in data["candles"]:
            start = datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc)   # nanosekundy odcinamy
            close = float(c["mid"]["c"])
            if c.get("complete"):
                candles.append((start + HOUR, close))
            if last is None or start > last[0]:
                last = (start, close)
        candles.sort()
        return Snapshot(candles, (min(last[0] + HOUR, now), last[1]) if last else None)


class GoldApi:
    name = "goldapi"
    base = "https://api.gold-api.com"

    def __init__(self, opener: Opener = _default_opener):
        self.opener = opener

    def fetch(self, asset: str, now: datetime, bars: int = 0) -> Snapshot:
        data = _json(self.opener, f"{self.base}/price/{asset}")
        if "price" not in data:
            raise ProviderError("gold-api: brak ceny w odpowiedzi")
        ts = now
        if data.get("updatedAt"):
            ts = datetime.fromisoformat(str(data["updatedAt"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        return Snapshot([], (min(ts, now), float(data["price"])))


def provider_from_env(opener: Opener = _default_opener):
    name = os.getenv("TAPE_PRICE_PROVIDER", "").strip().lower()
    if not name:
        return None
    if name == "twelvedata":
        key = os.getenv("TWELVEDATA_API_KEY", "")
        if not key:
            raise ValueError("TAPE_PRICE_PROVIDER=twelvedata wymaga TWELVEDATA_API_KEY")
        return TwelveData(key, opener)
    if name == "oanda":
        token = os.getenv("OANDA_TOKEN", "")
        if not token:
            raise ValueError("TAPE_PRICE_PROVIDER=oanda wymaga OANDA_TOKEN")
        return Oanda(token, os.getenv("OANDA_ENV", "practice"), opener)
    if name == "goldapi":
        return GoldApi(opener)
    raise ValueError(f"nieznany dostawca cen: {name}")


def save(session: Session, provider_name: str, asset: str, snap: Snapshot) -> int:
    from .news.store import add_prices

    added = add_prices(session, asset, snap.candles)
    if snap.quote:
        row = session.get(QuoteRow, asset)
        if row is None:
            session.add(QuoteRow(asset=asset, ts=snap.quote[0], price=snap.quote[1], provider=provider_name))
        elif snap.quote[0] >= row.ts:
            row.ts, row.price, row.provider = snap.quote[0], snap.quote[1], provider_name
    return added


def run_once(session: Session, provider, now: Optional[datetime] = None, bars: int = 48) -> Dict[str, object]:
    now = now or datetime.now(timezone.utc)
    report: Dict[str, object] = {}
    for asset in ASSETS:
        try:
            snap = provider.fetch(asset, now, bars)
            report[asset] = save(session, provider.name, asset, snap)
            session.commit()
        except Exception as exc:  # sieć / limit / format — drugi metal i tak próbujemy
            session.rollback()
            report[asset] = f"błąd: {exc}"
    return report


def mark(session: Session, asset: str) -> Optional[Tuple[datetime, float]]:
    """Najświeższa znana cena: bieżąca z `quotes` albo ostatnie zamknięcie świecy."""
    from .news.store import latest_price

    q = session.get(QuoteRow, asset)
    candle = latest_price(session, asset)
    options = [x for x in ((q.ts, q.price) if q else None, candle) if x]
    return max(options, key=lambda x: x[0]) if options else None


def quotes(session: Session, now: datetime) -> List[Dict[str, object]]:
    from .news.store import PriceRow

    out = []
    for asset in ASSETS:
        m = mark(session, asset)
        if m is None:
            continue
        ref = session.scalars(select(PriceRow).where(PriceRow.asset == asset, PriceRow.ts <= m[0] - timedelta(hours=24))
                              .order_by(PriceRow.ts.desc()).limit(1)).first()
        change = (m[1] / ref.price - 1) if ref and ref.price and m[0] - ref.ts <= timedelta(hours=30) else None
        q = session.get(QuoteRow, asset)
        out.append({"asset": asset, "price": m[1], "ts": m[0].isoformat(),
                    "age_minutes": int((now - m[0]).total_seconds() // 60), "change_24h": change,
                    "provider": q.provider if q else "import"})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ceny XAU/XAG dla Tape")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=300, help="Odstęp w sekundach (darmowe plany mają limity)")
    ap.add_argument("--backfill", type=int, default=0, help="Ile świec H1 pobrać przy starcie (np. 5000)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from .db import make_sessionmaker

    provider = provider_from_env()
    if provider is None:
        raise SystemExit("Ustaw TAPE_PRICE_PROVIDER (twelvedata | oanda | goldapi)")
    Session_ = make_sessionmaker()
    bars = args.backfill or 48
    while True:
        with Session_() as s:
            log.info("ceny (%s): %s", provider.name, run_once(s, provider, bars=bars))
        bars = 48
        if args.once:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()

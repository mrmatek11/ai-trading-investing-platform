"""Interactive Brokers (i Lynx) — raport Flex Query w XML (sekcja Trades).

Jak przygotować raport (raz): Portal → Performance & Reports → Flex Queries → Activity Flex Query,
sekcja „Trades” (pola m.in.: TradeID, Symbol, UnderlyingSymbol, AssetClass, Buy/Sell, Quantity,
TradePrice, Multiplier, IBCommission, Currency, Date/Time, Open/Close, FifoPnlRealized),
format XML. Plik pobierasz ręcznie albo przez Flex Web Service (fetch_statement poniżej).

Czas w raporcie jest w strefie ustawionej w zapytaniu Flex (domyślnie US/Eastern) — ustaw `tz`.
⚠️ Nazwy atrybutów zweryfikować na prawdziwym eksporcie z Twojego konta.
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Callable, List, Optional
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree  # plik od użytkownika: blokuje encje (XXE, „billion laughs”)
from defusedxml.common import DefusedXmlException

from .base import CashFlow, Fill, ImportResult, to_decimal, to_utc
from .instruments import normalize_symbol

NAME = "ibkr"
FLEX_BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
DATE_FORMATS = ("%Y%m%d;%H%M%S", "%Y-%m-%d;%H:%M:%S", "%Y%m%d %H%M%S", "%Y-%m-%d, %H:%M:%S", "%Y-%m-%d %H:%M:%S")


def detect(data: bytes) -> bool:
    head = data[:4096].lstrip()
    return head.startswith(b"<") and b"FlexQueryResponse" in data[:20000]


def _time(value: str, tz: str):
    last: Optional[Exception] = None
    for fmt in DATE_FORMATS:
        try:
            return to_utc(value, tz, fmt)
        except ValueError as exc:
            last = exc
    raise ValueError(f"nieznany format daty IBKR: {value!r}") from last


def _symbol(t) -> str:
    """Futures: kontrakt (np. GCZ6) — różne serie to różne pozycje; spot/CFD normalizujemy (XAUUSD)."""
    cat = (t.get("assetCategory") or "").upper()
    sym = (t.get("symbol") or "").strip()
    if cat in ("FUT", "FOP", "OPT"):
        return sym.replace(" ", "")
    return normalize_symbol(sym.replace(".", "").replace(" ", ""))


def parse(data: bytes, filename: str = "", tz: str = "America/New_York") -> ImportResult:
    result = ImportResult(detected=NAME)
    try:
        root = ElementTree.fromstring(data)
    except (ParseError, DefusedXmlException) as exc:
        result.errors.append(f"Niepoprawny lub niebezpieczny XML: {type(exc).__name__}")
        return result
    for c in root.findall(".//CashTransactions/CashTransaction"):
        if (c.get("type") or "") != "Deposits/Withdrawals" or (c.get("levelOfDetail") or "DETAIL").upper() == "SUMMARY":
            continue
        try:
            when = c.get("dateTime") or c.get("settleDate") or c.get("reportDate") or ""
            if len(when) == 8:
                when += ";000000"
            result.cash_flows.append(CashFlow(
                str(c.get("transactionID") or c.get("tradeID") or f"{when}:{c.get('amount')}"),
                _time(when, tz), to_decimal(c.get("amount")), c.get("currency") or "USD",
                (c.get("description") or "")[:200]))
        except (ValueError, TypeError) as exc:
            result.errors.append(f"wpłata/wypłata: {exc}")
    trades = root.findall(".//Trades/Trade")
    if not trades and result.cash_flows:
        return result
    if not trades:
        result.errors.append("Brak sekcji <Trades> w raporcie Flex — dodaj ją w definicji zapytania.")
        return result
    for i, t in enumerate(trades, start=1):
        try:
            if (t.get("levelOfDetail") or "EXECUTION").upper() not in ("EXECUTION", ""):
                continue  # pomijamy wiersze zbiorcze (ORDER / CLOSED_LOT), zostają wykonania
            side_raw = (t.get("buySell") or "").upper()
            if side_raw.startswith("BUY"):
                side = "buy"
            elif side_raw.startswith("SELL"):
                side = "sell"
            else:
                raise ValueError(f"nieznana strona: {side_raw!r}")
            trade_id = t.get("tradeID") or t.get("transactionID") or t.get("ibExecID")
            if not trade_id:
                raise ValueError("brak tradeID")
            closing = "C" in (t.get("openCloseIndicator") or "").upper()
            pnl = to_decimal(t.get("fifoPnlRealized"), allow_empty=True)
            result.fills.append(Fill(
                external_id=str(trade_id),
                ts=_time(t.get("dateTime") or f"{t.get('tradeDate')};{t.get('tradeTime', '000000')}", tz),
                symbol=_symbol(t),
                side=side,
                qty=abs(to_decimal(t.get("quantity"))),
                price=to_decimal(t.get("tradePrice")),
                contract_size=to_decimal(t.get("multiplier"), allow_empty=True) or Decimal(1),
                fee=to_decimal(t.get("ibCommission"), allow_empty=True) or Decimal(0),
                broker_pnl=pnl if closing else None,
                currency=t.get("currency") or "USD",
            ))
        except (ValueError, TypeError) as exc:
            result.errors.append(f"transakcja {i}: {exc}")
    return result


def fetch_statement(token: str, query_id: str, opener: Optional[Callable[[str], bytes]] = None,
                    attempts: int = 10, wait_seconds: float = 3.0) -> bytes:
    """Flex Web Service: SendRequest → kod referencyjny → GetStatement (raport bywa gotowy po kilku sekundach)."""
    if opener is None:
        def opener(url: str) -> bytes:
            req = urllib.request.Request(url, headers={"User-Agent": "tape/0.1"})
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — stały endpoint IBKR
                return resp.read()

    q = urllib.parse.urlencode({"t": token, "q": query_id, "v": "3"})
    first = ElementTree.fromstring(opener(f"{FLEX_BASE}/SendRequest?{q}"))
    if (first.findtext("Status") or "") != "Success":
        raise RuntimeError(f"IBKR odrzucił zapytanie: {first.findtext('ErrorMessage') or 'nieznany błąd'}")
    ref = first.findtext("ReferenceCode")
    q2 = urllib.parse.urlencode({"t": token, "q": ref, "v": "3"})
    for _ in range(attempts):
        body = opener(f"{FLEX_BASE}/GetStatement?{q2}")
        if b"FlexQueryResponse" in body[:2000]:
            return body
        status = ElementTree.fromstring(body)
        if (status.findtext("ErrorCode") or "") not in ("1019", ""):   # 1019 = raport jeszcze się generuje
            raise RuntimeError(f"IBKR: {status.findtext('ErrorMessage')}")
        time.sleep(wait_seconds)
    raise RuntimeError("IBKR nie przygotował raportu na czas — spróbuj ponownie za chwilę.")

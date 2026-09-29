"""MetaTrader 5 — lista transakcji (Deals) z raportu historii albo z EA „Tape Sync”.

Kolumny: Time, Deal, Symbol, Type (buy/sell/balance…), Direction (in/out/inout), Volume, Price,
Commission, Fee, Swap, Profit. Czas to czas serwera brokera — domyślnie przyjmujemy UTC+2
(typowe dla serwerów MT5); ustaw `tz` na właściwą strefę, np. "Etc/GMT-3" dla UTC+3.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List

from .base import CashFlow, Fill, ImportResult, has_columns, iter_with_errors, pick, read_rows, require, to_decimal, to_utc
from .instruments import default_contract_size, normalize_symbol

NAME = "mt5"
SKIP_TYPES = {"balance", "credit", "charge", "correction", "bonus", "commission", "deposit", "withdrawal"}


def detect(rows) -> bool:
    return has_columns(rows, "deal", "symbol", "direction", "volume", "price")


def parse(data: bytes, filename: str, tz: str = "Etc/GMT-2", contract_sizes: Dict[str, Decimal] | None = None) -> ImportResult:
    rows = read_rows(data, filename)
    result = ImportResult(detected=NAME)
    if not detect(rows):
        result.errors.append("To nie wygląda na listę transakcji (Deals) z MetaTrader 5.")
        return result
    sizes = contract_sizes or {}

    def row_to_fills(row: Dict[str, object]) -> List[Fill]:
        kind = str(require(row, "type")).strip().lower()
        if kind == "balance":                 # wpłata / wypłata — do stóp zwrotu, nie do pozycji
            amount = to_decimal(pick(row, "profit"), allow_empty=True)
            if amount:
                result.cash_flows.append(CashFlow(str(require(row, "deal")).split(".")[0],
                                                  to_utc(require(row, "time"), tz), amount, note="MT5 balance"))
            return []
        if kind in SKIP_TYPES or not pick(row, "symbol"):
            return []
        if kind not in ("buy", "sell"):
            raise ValueError(f"nieobsługiwany typ transakcji: {kind}")
        symbol = normalize_symbol(require(row, "symbol"))
        direction = str(pick(row, "direction") or "").strip().lower()
        costs = sum((to_decimal(pick(row, c), allow_empty=True) or Decimal(0))
                    for c in ("commission", "fee", "swap"))
        profit = to_decimal(pick(row, "profit"), allow_empty=True)
        return [Fill(
            external_id=str(require(row, "deal")).split(".")[0],
            ts=to_utc(require(row, "time"), tz),
            symbol=symbol, side=kind,
            qty=to_decimal(require(row, "volume")),
            price=to_decimal(require(row, "price")),
            contract_size=sizes.get(symbol, default_contract_size(symbol)),
            fee=costs,
            broker_pnl=profit if direction in ("out", "inout", "out_by") else None,
        )]

    iter_with_errors(rows, row_to_fills, result)
    return result

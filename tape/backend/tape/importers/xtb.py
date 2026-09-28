"""XTB (xStation 5) — eksport „zamknięte pozycje” (XLSX / CSV).

Każdy wiersz to zamknięta pozycja → dwa fill-e (otwarcie i zamknięcie).
Czas w pliku jest lokalny (domyślnie Europe/Warsaw). Kolumny są dopasowywane po aliasach,
bo nazwy różnią się między wersjami raportu — ⚠️ zweryfikować na aktualnym eksporcie.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List

from .base import Fill, ImportResult, has_columns, iter_with_errors, pick, read_rows, require, to_decimal, to_utc
from .instruments import default_contract_size, infer_contract_size, normalize_symbol

NAME = "xtb"


def detect(rows) -> bool:
    return has_columns(rows, "position", "open_time", "close_time", "open_price", "close_price")


def parse(data: bytes, filename: str, tz: str = "Europe/Warsaw") -> ImportResult:
    rows = read_rows(data, filename)
    result = ImportResult(detected=NAME)
    if not detect(rows):
        result.errors.append("To nie wygląda na eksport zamkniętych pozycji z XTB "
                             "(brak kolumn Position / Open time / Close time).")
        return result

    def row_to_fills(row: Dict[str, object]) -> List[Fill]:
        pos_id = str(require(row, "position", "position_id")).split(".")[0]
        symbol = normalize_symbol(require(row, "symbol"))
        side_raw = str(require(row, "type", "side")).strip().upper()
        if side_raw.startswith("BUY"):
            direction = 1
        elif side_raw.startswith("SELL"):
            direction = -1
        else:
            raise ValueError(f"nieznany typ pozycji: {side_raw}")
        qty = to_decimal(require(row, "volume", "lots"))
        open_px = to_decimal(require(row, "open_price"))
        close_px = to_decimal(require(row, "close_price"))
        opened = to_utc(require(row, "open_time"), tz)
        closed = to_utc(require(row, "close_time"), tz)
        gross = to_decimal(pick(row, "gross_p_l", "gross_pl", "profit", "p_l"), allow_empty=True)
        costs = sum((to_decimal(pick(row, c), allow_empty=True) or Decimal(0))
                    for c in ("commission", "swap", "rollover"))
        sl = to_decimal(pick(row, "sl", "stop_loss"), allow_empty=True)
        size = (infer_contract_size(open_px, close_px, qty, direction, gross)
                or default_contract_size(symbol))
        open_side, close_side = ("buy", "sell") if direction == 1 else ("sell", "buy")
        return [
            Fill(external_id=f"{pos_id}:open", ts=opened, symbol=symbol, side=open_side, qty=qty,
                 price=open_px, contract_size=size, stop_loss=sl if sl else None),
            Fill(external_id=f"{pos_id}:close", ts=closed, symbol=symbol, side=close_side, qty=qty,
                 price=close_px, contract_size=size, fee=costs, broker_pnl=gross),
        ]

    iter_with_errors(rows, row_to_fills, result)
    return result

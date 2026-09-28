"""Uniwersalny import CSV/XLSX według mapowania kolumn.

Mapowanie proponuje AI (nagłówki + próbka wierszy), zatwierdza użytkownik, a zatwierdzone
mapowanie staje się szablonem dla danego brokera. Tutaj tylko deterministyczne zastosowanie mapowania.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from .base import Fill, ImportResult, iter_with_errors, norm_header, read_rows, to_decimal, to_utc
from .instruments import default_contract_size, normalize_symbol

NAME = "generic"
REQUIRED = ("id", "time", "symbol", "side", "qty", "price")
OPTIONAL = ("fee", "pnl", "stop_loss", "contract_size")


@dataclass
class Mapping:
    """Pole kanoniczne → nazwa kolumny w pliku."""

    columns: Dict[str, str]
    tz: str = "UTC"
    date_format: Optional[str] = None
    buy_values: List[str] = field(default_factory=lambda: ["buy", "b", "long", "kupno"])
    sell_values: List[str] = field(default_factory=lambda: ["sell", "s", "short", "sprzedaz", "sprzedaż"])

    def missing(self) -> List[str]:
        return [f for f in REQUIRED if f not in self.columns]


def parse(data: bytes, filename: str, mapping: Mapping) -> ImportResult:
    result = ImportResult(detected=NAME)
    missing = mapping.missing()
    if missing:
        result.errors.append(f"Mapowanie nie obejmuje wymaganych pól: {', '.join(missing)}")
        return result
    rows = read_rows(data, filename)
    cols = {k: norm_header(v) for k, v in mapping.columns.items()}
    buys = {v.lower() for v in mapping.buy_values}
    sells = {v.lower() for v in mapping.sell_values}

    def get(row, field_name):
        col = cols.get(field_name)
        return row.get(col) if col else None

    def row_to_fills(row) -> List[Fill]:
        side_raw = str(get(row, "side")).strip().lower()
        if side_raw in buys:
            side = "buy"
        elif side_raw in sells:
            side = "sell"
        else:
            raise ValueError(f"nieznana strona transakcji: {side_raw!r}")
        symbol = normalize_symbol(get(row, "symbol"))
        size = to_decimal(get(row, "contract_size"), allow_empty=True)
        return [Fill(
            external_id=str(get(row, "id")),
            ts=to_utc(get(row, "time"), mapping.tz, mapping.date_format),
            symbol=symbol, side=side,
            qty=abs(to_decimal(get(row, "qty"))),
            price=to_decimal(get(row, "price")),
            contract_size=size or default_contract_size(symbol),
            fee=to_decimal(get(row, "fee"), allow_empty=True) or Decimal(0),
            broker_pnl=to_decimal(get(row, "pnl"), allow_empty=True),
            stop_loss=to_decimal(get(row, "stop_loss"), allow_empty=True),
        )]

    iter_with_errors(rows, row_to_fills, result)
    return result

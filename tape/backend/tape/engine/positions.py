"""Silnik pozycji: fill-e → zamknięte pozycje (od zera do zera), FIFO, Decimal.

Zasady:
- Pozycja trwa od otwarcia z płaskiego stanu do powrotu do zera; częściowe zamknięcia i dokładki
  należą do tej samej pozycji.
- Fill przechodzący przez zero (odwrócenie) zamyka bieżącą pozycję i otwiera nową z nadwyżką.
- PnL: gdy broker raportuje PnL dla wszystkich zamknięć pozycji, używamy go (źródło prawdy dla kursów
  walut i specyfikacji kontraktu); w przeciwnym razie liczymy z cen × wielkość kontraktu.
- R-multiple tylko gdy znamy początkowy stop loss.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Deque, Dict, Iterable, List, Optional

from ..importers.base import Fill

ZERO = Decimal(0)


@dataclass
class Lot:
    qty: Decimal
    price: Decimal


@dataclass
class Position:
    symbol: str
    direction: int                    # 1 long, -1 short
    opened_at: datetime
    closed_at: Optional[datetime] = None
    qty: Decimal = ZERO               # maksymalna wielkość w trakcie pozycji
    entry_value: Decimal = ZERO       # Σ qty × cena otwarć
    exit_value: Decimal = ZERO        # Σ qty × cena zamknięć
    opened_qty: Decimal = ZERO
    closed_qty: Decimal = ZERO
    contract_size: Decimal = Decimal(1)
    fees: Decimal = ZERO
    computed_pnl: Decimal = ZERO      # z cen, FIFO
    broker_pnl: Decimal = ZERO
    broker_pnl_complete: bool = True
    initial_stop: Optional[Decimal] = None
    open_value: Decimal = ZERO        # Σ qty × cena lotów jeszcze otwartych (FIFO) — dla portfela
    fill_ids: List[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stabilny identyfikator: pozycja zaczyna się zawsze od tego samego fill-a otwierającego,
        więc klucz przeżywa ponowne przeliczenie pozycji od zera (notatki z journala się nie gubią)."""
        return hashlib.sha1(self.fill_ids[0].encode()).hexdigest()[:12]

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def open_qty(self) -> Decimal:
        return self.opened_qty - self.closed_qty

    @property
    def avg_open_price(self) -> Optional[Decimal]:
        """Średnia cena lotów, które zostały otwarte (FIFO) — różni się od avg_entry po częściowych zamknięciach."""
        return self.open_value / self.open_qty if self.open_qty else None

    @property
    def avg_entry(self) -> Decimal:
        return self.entry_value / self.opened_qty if self.opened_qty else ZERO

    @property
    def avg_exit(self) -> Optional[Decimal]:
        return self.exit_value / self.closed_qty if self.closed_qty else None

    @property
    def gross_pnl(self) -> Decimal:
        return self.broker_pnl if (self.broker_pnl_complete and self.closed_qty) else self.computed_pnl

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl + self.fees

    @property
    def risk(self) -> Optional[Decimal]:
        """Kwota ryzyka (1R) dla pierwszego wejścia i początkowego SL."""
        if self.initial_stop is None:
            return None
        dist = (self.avg_entry - self.initial_stop) * self.direction
        if dist <= 0:
            return None
        return dist * self.opened_qty * self.contract_size

    @property
    def r_multiple(self) -> Optional[Decimal]:
        risk = self.risk
        if risk is None or risk == 0 or self.is_open:
            return None
        return self.net_pnl / risk


def build_positions(fills: Iterable[Fill]) -> List[Position]:
    """Zbuduj pozycje ze wszystkich fill-i (dowolna kolejność na wejściu)."""
    by_symbol: Dict[str, List[Fill]] = defaultdict(list)
    for f in fills:
        by_symbol[f.symbol].append(f)

    positions: List[Position] = []
    for symbol, items in by_symbol.items():
        items.sort(key=lambda f: (f.ts, f.external_id))
        lots: Deque[Lot] = deque()
        current: Optional[Position] = None
        for f in items:
            sign = 1 if f.side == "buy" else -1
            remaining = f.qty
            fee_left = f.fee
            first_touch = True
            while remaining > 0:
                if current is None:
                    current = Position(symbol=symbol, direction=sign, opened_at=f.ts,
                                       contract_size=f.contract_size, initial_stop=f.stop_loss)
                    current.fill_ids.append(f.external_id)
                    positions.append(current)
                if sign == current.direction:
                    lots.append(Lot(remaining, f.price))
                    current.opened_qty += remaining
                    current.entry_value += remaining * f.price
                    held = sum(l.qty for l in lots)
                    current.qty = max(current.qty, held)
                    if current.initial_stop is None and f.stop_loss is not None:
                        current.initial_stop = f.stop_loss
                    remaining = ZERO
                else:
                    # zamknięcie FIFO
                    closed_here = ZERO
                    while remaining > 0 and lots:
                        lot = lots[0]
                        take = min(lot.qty, remaining)
                        current.computed_pnl += (f.price - lot.price) * take * current.direction * current.contract_size
                        lot.qty -= take
                        remaining -= take
                        closed_here += take
                        if lot.qty == 0:
                            lots.popleft()
                    current.closed_qty += closed_here
                    current.exit_value += closed_here * f.price
                    if first_touch:
                        if f.broker_pnl is None:
                            current.broker_pnl_complete = False
                        else:
                            current.broker_pnl += f.broker_pnl
                    if not lots:
                        current.closed_at = f.ts
                # prowizję przypisujemy do pozycji, której dotyczy pierwsza część fill-a
                if first_touch:
                    current.fees += fee_left
                    if f.external_id not in current.fill_ids:
                        current.fill_ids.append(f.external_id)
                    fee_left = ZERO
                    first_touch = False
                if current.closed_at is not None:
                    current = None
        if current is not None:
            current.open_value = sum((l.qty * l.price for l in lots), ZERO)
    positions.sort(key=lambda p: p.opened_at)
    return positions

"""Kalkulator wielkości pozycji — pod złoto i srebro, gdzie sizing z EUR/USD szybko rozwala dzienny limit.

Wszystko na Decimal; wielkość zaokrąglana W DÓŁ do kroku lota, więc ryzyko nigdy nie przekracza zadanego.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Optional


@dataclass
class SizeResult:
    lots: Decimal                    # 0, gdy nawet minimalny lot przekracza ryzyko
    risk_budget: Decimal             # ile wolno stracić na SL
    risk_actual: Decimal             # ile faktycznie stracisz na SL przy wyliczonej wielkości
    stop_distance: Decimal           # w cenie
    value_per_point: Decimal         # zmiana wartości pozycji przy ruchu ceny o 1,0
    notional: Decimal
    min_lot_risk: Decimal            # ryzyko przy minimalnym locie
    daily_range_loss: Optional[Decimal] = None   # strata, gdyby cena przeszła cały dzienny zasięg przeciwko pozycji
    daily_limit_share: Optional[Decimal] = None  # jaka część dziennego limitu idzie na ten SL
    warnings: tuple = ()


def position_size(balance: Decimal, risk_pct: Decimal, entry: Decimal, stop: Decimal,
                  contract_size: Decimal = Decimal(100), lot_step: Decimal = Decimal("0.01"),
                  min_lot: Decimal = Decimal("0.01"), max_lot: Optional[Decimal] = None,
                  daily_range: Optional[Decimal] = None, daily_loss_limit: Optional[Decimal] = None) -> SizeResult:
    if balance <= 0 or risk_pct <= 0 or entry <= 0 or contract_size <= 0 or lot_step <= 0:
        raise ValueError("saldo, ryzyko, cena wejścia, kontrakt i krok lota muszą być dodatnie")
    distance = abs(entry - stop)
    if distance == 0:
        raise ValueError("stop loss nie może być równy cenie wejścia")

    budget = balance * risk_pct / Decimal(100)
    loss_per_lot = distance * contract_size
    raw = budget / loss_per_lot
    lots = (raw / lot_step).to_integral_value(rounding=ROUND_DOWN) * lot_step
    warnings = []
    if max_lot is not None and lots > max_lot:
        lots = max_lot
        warnings.append("Wielkość obcięta do maksymalnego lota brokera.")
    if lots < min_lot:
        lots = Decimal(0)
        warnings.append("Minimalny lot przekracza budżet ryzyka — poszerz budżet albo zbliż stop loss.")

    risk_actual = lots * loss_per_lot
    result = SizeResult(
        lots=lots, risk_budget=budget.quantize(Decimal("0.01")), risk_actual=risk_actual.quantize(Decimal("0.01")),
        stop_distance=distance, value_per_point=(lots * contract_size),
        notional=(lots * contract_size * entry).quantize(Decimal("0.01")),
        min_lot_risk=(min_lot * loss_per_lot).quantize(Decimal("0.01")),
    )
    if daily_range is not None and daily_range > 0:
        result.daily_range_loss = (lots * contract_size * daily_range).quantize(Decimal("0.01"))
        if distance > daily_range:
            warnings.append("Stop loss dalej niż średni dzienny zasięg — pozycja może trwać dni.")
        if distance < daily_range * Decimal("0.1"):
            warnings.append("Stop loss poniżej 10% dziennego zasięgu — duże ryzyko wybicia szumem i spreadem.")
    if daily_loss_limit is not None and daily_loss_limit > 0:
        result.daily_limit_share = (risk_actual / daily_loss_limit).quantize(Decimal("0.0001"))
        if risk_actual > daily_loss_limit:
            warnings.append("Jeden stop loss przekracza dzienny limit straty.")
        elif result.daily_limit_share > Decimal("0.5"):
            warnings.append("Jeden stop loss zjada ponad połowę dziennego limitu.")
    result.warnings = tuple(warnings)
    return result

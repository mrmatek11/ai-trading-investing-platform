"""Normalizacja symboli i wielkości kontraktów między brokerami."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

# Aliasów używają brokerzy CFD (XTB: GOLD / SILVER) i serwery MT5 (sufiksy .pro, m, +, _i…)
ALIASES = {
    "GOLD": "XAUUSD", "XAU": "XAUUSD", "XAUUSD": "XAUUSD",
    "SILVER": "XAGUSD", "XAG": "XAGUSD", "XAGUSD": "XAGUSD",
}

# Jednostki bazowe na 1 lot. Brokerzy mogą mieć inne — wtedy wielkość wyliczamy z PnL (patrz niżej).
CONTRACT_SIZES = {
    "XAUUSD": Decimal(100),     # uncje
    "XAGUSD": Decimal(5000),    # uncje
}
FX_LOT = Decimal(100000)
_FX = re.compile(r"^[A-Z]{6}$")
_SUFFIX = re.compile(r"(\.[A-Za-z0-9]+|[_\-][A-Za-z0-9]+|[mMcC+#!]+)$")


def normalize_symbol(raw: str) -> str:
    s = str(raw).strip().upper()
    if s in ALIASES:
        return ALIASES[s]
    base = _SUFFIX.sub("", s)
    return ALIASES.get(base, base)


def default_contract_size(symbol: str) -> Decimal:
    if symbol in CONTRACT_SIZES:
        return CONTRACT_SIZES[symbol]
    if _FX.match(symbol):
        return FX_LOT
    return Decimal(1)


def infer_contract_size(open_price: Decimal, close_price: Decimal, qty: Decimal, direction: int,
                        gross_pnl: Optional[Decimal]) -> Optional[Decimal]:
    """Wielkość kontraktu z PnL raportowanego przez brokera: pnl = kierunek × Δcena × qty × size.

    Zaokrąglona do rzędu wielkości typowych kontraktów, żeby spread/kurs waluty nie dawał 99,7 zamiast 100.
    """
    if gross_pnl is None or qty == 0:
        return None
    move = (close_price - open_price) * direction * qty
    if move == 0:
        return None
    size = gross_pnl / move
    if size <= 0:
        return None
    for candidate in (Decimal(1), Decimal(10), Decimal(100), Decimal(1000), Decimal(5000),
                      Decimal(10000), Decimal(100000)):
        if abs(size - candidate) / candidate < Decimal("0.05"):
            return candidate
    return None

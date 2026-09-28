"""Importery transakcji: rozpoznanie formatu i parsowanie do kanonicznych fill-i."""

from __future__ import annotations

from typing import Optional

from . import generic, mt5, xtb
from .base import Fill, ImportResult, read_rows

BROKERS = {"xtb": xtb, "mt5": mt5}


def detect_broker(data: bytes, filename: str) -> Optional[str]:
    rows = read_rows(data, filename)
    for name, module in BROKERS.items():
        if module.detect(rows):
            return name
    return None


def parse_file(data: bytes, filename: str, broker: Optional[str] = None, tz: Optional[str] = None,
               mapping: Optional[generic.Mapping] = None) -> ImportResult:
    if mapping is not None:
        return generic.parse(data, filename, mapping)
    broker = broker or detect_broker(data, filename)
    if broker not in BROKERS:
        return ImportResult(errors=["Nie rozpoznano formatu pliku. Użyj importu z mapowaniem kolumn."])
    module = BROKERS[broker]
    return module.parse(data, filename, tz=tz) if tz else module.parse(data, filename)


__all__ = ["Fill", "ImportResult", "BROKERS", "detect_broker", "parse_file", "generic"]

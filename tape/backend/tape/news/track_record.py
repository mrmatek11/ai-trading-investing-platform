"""Trafność nastawienia newsów: każda zapisana ocena vs. późniejszy ruch ceny.

Liczymy tylko oceny kierunkowe (long/short). Kolejne oceny z tego samego horyzontu nachodzą na siebie,
więc bierzemy co najwyżej jedną ocenę na okno `horizon` (inaczej t-stat byłby zawyżony).
Porównanie z punktem odniesienia: „zawsze long” w tych samych momentach.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

MIN_OBSERVATIONS = 200          # próg z PRODUCT_SPEC 4.6, zanim UI pokaże etykiety LONG/SHORT
MIN_SPAN = timedelta(days=365)


@dataclass
class TrackRecord:
    observations: int
    hit_rate: Optional[float]
    mean_signed_return: Optional[float]      # średni zwrot w kierunku oceny
    t_stat: Optional[float]
    baseline_long_return: Optional[float]    # „zawsze long” w tych samych chwilach
    span_days: float
    significant: bool

    @property
    def labels_allowed(self) -> bool:
        return self.significant and self.observations >= MIN_OBSERVATIONS and self.span_days >= MIN_SPAN.days


def _price_at(times: List[datetime], prices: List[float], t: datetime) -> Optional[float]:
    """Ostatnia znana cena w chwili t (bez zaglądania w przyszłość)."""
    i = bisect.bisect_right(times, t) - 1
    return prices[i] if i >= 0 else None


def evaluate(snapshots: Sequence[Tuple[datetime, float, str]], price_series: Sequence[Tuple[datetime, float]],
             horizon: timedelta = timedelta(hours=24)) -> TrackRecord:
    series = sorted(price_series)
    times = [t for t, _ in series]
    prices = [p for _, p in series]
    signed, longs, hits = [], [], 0
    last_used: Optional[datetime] = None
    first = last = None
    for ts, _score, label in sorted(snapshots):
        if label not in ("long", "short"):
            continue
        if last_used is not None and ts - last_used < horizon:
            continue
        if not times or ts + horizon > times[-1]:
            continue  # wynik jeszcze nieznany
        p0, p1 = _price_at(times, prices, ts), _price_at(times, prices, ts + horizon)
        if not p0 or p1 is None:
            continue
        ret = p1 / p0 - 1
        d = 1 if label == "long" else -1
        signed.append(d * ret)
        longs.append(ret)
        hits += 1 if d * ret > 0 else 0
        last_used = ts
        first = first or ts
        last = ts
    n = len(signed)
    if n == 0:
        return TrackRecord(0, None, None, None, None, 0.0, False)
    mean = sum(signed) / n
    t = None
    if n > 1:
        sd = math.sqrt(sum((x - mean) ** 2 for x in signed) / (n - 1))
        t = mean / (sd / math.sqrt(n)) if sd > 0 else None
    span = (last - first).total_seconds() / 86400 if first and last else 0.0
    return TrackRecord(n, hits / n, mean, t, sum(longs) / n, span, t is not None and t >= 2)

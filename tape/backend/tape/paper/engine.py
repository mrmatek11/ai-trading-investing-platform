"""Silnik paper tradingu: London ORB po dniu NR7 na świecach M15 — bez pandas, bez stanu.

To jest wierne przeniesienie zamrożonych reguł z `research/intraday_lab.py` (`london_orb_nr7`, `simulate`,
`run_day`, `daily_context`). Zgodność sprawdzono transakcja po transakcji na pełnych danych badania
(docs/BACKTEST_RESULTS.md, karta zamrożenia). NIE ZMIENIAJ tego pliku bez nowej wersji strategii:
jego SHA-256 jest częścią odcisku wersji, a test pilnuje, żeby zmiana nie przeszła po cichu.

Świece: czas OTWARCIA w UTC, ceny tej samej strony co w badaniu (bid). Dzień handlowy zaczyna się
o 17:00 czasu Nowego Jorku; godziny Londynu liczymy tylko w części „po północy” (dmin ≥ 300).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

LONDON, NEW_YORK = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")
BAR = timedelta(minutes=15)


@dataclass(frozen=True)
class Bar:
    ts: datetime                      # otwarcie świecy, UTC
    open: float
    high: float
    low: float
    close: float
    spread: Optional[float] = None    # ask − bid, jeśli dostawca go podaje


@dataclass(frozen=True)
class Order:
    direction: int
    start: datetime
    deadline: datetime
    entry: float
    stop: float
    cancel_if_no_fill: Optional[datetime]


@dataclass(frozen=True)
class Trade:
    direction: int
    entry_time: datetime
    entry: float
    exit: float
    exit_time: datetime
    reason: str                       # sl | time
    gross: float                      # USD/oz, przed kosztami
    stop: float
    entry_bar: Bar
    exit_bar: Bar


@dataclass(frozen=True)
class DayResult:
    day: date
    status: str                       # trade | no_nr7 | no_setup | no_fill | data_gap | pending
    orders: Tuple[Order, ...] = ()
    trade: Optional[Trade] = None
    note: str = ""
    nr7: bool = False                 # czy wczorajszy dzień był NR7 (dzień „aktywny”)


@dataclass(frozen=True)
class _S:                             # świeca z czasem sesji
    bar: Bar
    day: date
    dmin: int
    ldn_min: int


def _session(b: Bar) -> _S:
    ny = b.ts.astimezone(NEW_YORK)
    ldn = b.ts.astimezone(LONDON)
    ny_min = ny.hour * 60 + ny.minute
    day = (ny.replace(tzinfo=None) + timedelta(hours=7)).date()        # 17:00 NY = początek nowego dnia
    return _S(b, day, (ny_min - 17 * 60) % 1440, ldn.hour * 60 + ldn.minute)


def group_days(bars: Sequence[Bar]) -> List[Tuple[date, List[_S]]]:
    """Świece (dowolna kolejność, bez duplikatów czasu) → dni handlowe po kolei."""
    out: Dict[date, List[_S]] = {}
    for b in sorted(bars, key=lambda x: x.ts):
        s = _session(b)
        out.setdefault(s.day, []).append(s)
    return sorted(out.items())


def nr7_flags(days: Sequence[Tuple[date, List[_S]]]) -> Dict[date, bool]:
    """Czy WCZORAJSZY dzień handlowy (z tych w danych) miał najwęższy zakres z ostatnich 7."""
    ranges = [max(s.bar.high for s in d) - min(s.bar.low for s in d) for _, d in days]
    flags: Dict[date, bool] = {}
    for i, (day, _) in enumerate(days):
        j = i - 1                                                       # kontekst znany przed startem dnia
        flags[day] = j >= 6 and ranges[j] <= min(ranges[j - 6: j + 1])
    return flags


def _first(day: Sequence[_S], minute: int) -> Optional[datetime]:
    for s in day:
        if s.dmin >= 300 and s.ldn_min >= minute:
            return s.bar.ts
    return None


def london_orders(day: Sequence[_S], params: dict) -> List[Order]:
    rm = params["range_min"]
    rng = [s.bar for s in day if s.dmin >= 300 and 8 * 60 <= s.ldn_min < 8 * 60 + rm]
    start, cancel, end = _first(day, 8 * 60 + rm), _first(day, 12 * 60), _first(day, 16 * 60)
    if not rng or start is None or end is None or len(rng) < 2:
        return []
    hi, lo = max(b.high for b in rng), min(b.low for b in rng)
    width = hi - lo
    if width <= 0:
        return []
    buf = params.get("buffer", 0.0) * width
    orders = []
    for d, lvl, opp in ((1, hi + buf, lo), (-1, lo - buf, hi)):
        stop = opp if params["stop"] == "range" else lvl - d * width * 0.5
        orders.append(Order(d, start, end, lvl, stop, cancel))
    return orders


def planned_orders(day_date: date, day: Sequence[_S], params: dict) -> List[Order]:
    """Dla dnia, który jeszcze trwa: poziomy po zamknięciu zakresu, z godzinami z kalendarza
    (świeca z 16:00 jeszcze nie istnieje). Tylko do podglądu — rozliczenie zawsze przez london_orders."""
    rm = params["range_min"]
    rng = [s.bar for s in day if s.dmin >= 300 and 8 * 60 <= s.ldn_min < 8 * 60 + rm]
    if len(rng) < max(2, rm // 15):
        return []
    at = lambda m: datetime(day_date.year, day_date.month, day_date.day, m // 60, m % 60, tzinfo=LONDON)  # noqa: E731
    start, cancel, end = at(8 * 60 + rm), at(12 * 60), at(16 * 60)
    hi, lo = max(b.high for b in rng), min(b.low for b in rng)
    width = hi - lo
    if width <= 0:
        return []
    buf = params.get("buffer", 0.0) * width
    return [Order(d, start, end, lvl, opp if params["stop"] == "range" else lvl - d * width * 0.5, cancel)
            for d, lvl, opp in ((1, hi + buf, lo), (-1, lo - buf, hi))]


def simulate(day: Sequence[_S], order: Order) -> Optional[Trade]:
    sub = [s.bar for s in day if order.start <= s.bar.ts < order.deadline]
    if not sub:
        return None
    last_fill = len(sub) if order.cancel_if_no_fill is None else sum(1 for b in sub if b.ts < order.cancel_if_no_fill)
    d = order.direction
    i0, entry = None, None
    for i in range(last_fill):
        b = sub[i]
        if d == 1 and b.high >= order.entry:
            i0, entry = i, max(b.open, order.entry)          # luka ponad poziom → wypełnienie po otwarciu
            break
        if d == -1 and b.low <= order.entry:
            i0, entry = i, min(b.open, order.entry)
            break
    if i0 is None:
        return None
    stop = order.stop
    for i in range(i0, len(sub)):
        b = sub[i]
        if (d == 1 and b.low <= stop) or (d == -1 and b.high >= stop):
            px = stop if i == i0 or (d == 1 and b.open > stop) or (d == -1 and b.open < stop) else b.open
            return Trade(d, sub[i0].ts, entry, px, b.ts, "sl", d * (px - entry), stop, sub[i0], b)
    last = sub[-1]
    return Trade(d, sub[i0].ts, entry, last.close, last.ts, "time", d * (last.close - entry), stop, sub[i0], last)


def run_day(day: Sequence[_S], orders: Sequence[Order]) -> Optional[Trade]:
    """OCO: wygrywa zlecenie wypełnione najwcześniej; przy remisie pierwsze z listy (long)."""
    best = None
    for o in orders:
        t = simulate(day, o)
        if t is not None and (best is None or t.entry_time < best.entry_time):
            best = t
    return best


# ─── kompletność danych (tylko tryb na żywo — w badaniu jej nie było) ──────────

MIN_BARS_PER_DAY = 60          # pełny dzień złota to ~92 świece; mniej = dziura u dostawcy, NR7 byłby fałszywy


def data_gap(days: Sequence[Tuple[date, List[_S]]], i: int, params: dict) -> str:
    """Pusty napis = dane kompletne; inaczej powód pominięcia dnia."""
    if i < 7:
        return "za mało historii do NR7 (potrzeba 7 poprzednich dni)"
    short = [str(d) for d, bars in days[i - 7: i] if len(bars) < MIN_BARS_PER_DAY]
    if short:
        return f"niepełne dni w oknie NR7: {', '.join(short)}"
    day = days[i][1]
    rm = params["range_min"]
    have = sum(1 for s in day if s.dmin >= 300 and 8 * 60 <= s.ldn_min < 8 * 60 + rm)
    if have < rm // 15:
        return f"zakres 08:00 Londynu ma {have} z {rm // 15} świec"
    return ""


def evaluate(bars: Sequence[Bar], params: dict, now: Optional[datetime] = None,
             from_day: Optional[date] = None, check_gaps: bool = True) -> List[DayResult]:
    """Wyniki dni handlowych od `from_day`. Dzień jest rozstrzygnięty dopiero po zamknięciu świecy z 16:00 Londynu
    (albo gdy są już świece następnego dnia); wcześniej status „pending” i zero transakcji."""
    days = group_days(bars)
    flags = nr7_flags(days)
    out: List[DayResult] = []
    for i, (day, sess) in enumerate(days):
        if from_day is not None and day < from_day:
            continue
        orders = tuple(london_orders(sess, params))
        end = orders[0].deadline if orders else _first(sess, 16 * 60)
        later = i + 1 < len(days)
        nr7 = flags[day]
        if not later and (end is None or (now is not None and now < end + BAR)):
            out.append(DayResult(day, "pending", orders or tuple(planned_orders(day, sess, params)), nr7=nr7))
            continue
        if not nr7:
            out.append(DayResult(day, "no_nr7"))
            continue
        gap = data_gap(days, i, params) if check_gaps else ""
        if gap:
            out.append(DayResult(day, "data_gap", orders, note=gap, nr7=True))
            continue
        if not orders:
            out.append(DayResult(day, "no_setup", note="brak zakresu 08:00–09:00 albo świecy z 16:00 Londynu", nr7=True))
            continue
        t = run_day(sess, orders)
        out.append(DayResult(day, "trade" if t else "no_fill", orders, t, nr7=True))
    return out

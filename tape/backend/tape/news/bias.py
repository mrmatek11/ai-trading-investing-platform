"""Nastawienie newsów dla XAU / XAG — agregacja w kodzie (sekcja 4.6 specyfikacji).

AI klasyfikuje zdarzenie (kierunek, siła, horyzont, nowość, pewność); tutaj z tych ocen liczymy
jedną liczbę −1…+1 per aktywo. Zanik w czasie zależy od horyzontu zdarzenia, a „shrinkage”
(PRIOR_WEIGHT) ściąga wynik do zera, gdy zdarzeń jest mało — kilka słabych newsów nie daje
„silnego long”.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

HALF_LIFE_HOURS = {"intraday": 6.0, "days": 48.0, "weeks": 240.0}
NOVELTY_WEIGHT = {"new": 1.0, "developing": 0.6, "already_known": 0.2}
PRIOR_WEIGHT = 6.0   # ≈ jedno-dwa silne, świeże zdarzenia; słabszy dowód ściągany do zera
NEUTRAL_BAND = 0.15
STRONG_BAND = 0.45


@dataclass(frozen=True)
class Impact:
    direction: int          # -1, 0, 1
    magnitude: int          # 1..5
    horizon: str = "days"   # intraday | days | weeks


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    category: str                           # conflict | cb | macro | supply | market
    lat: float
    lon: float
    occurred_at: datetime                   # UTC; w przyszłości = zdarzenie oczekiwane
    impacts: Dict[str, Impact] = field(default_factory=dict)
    novelty: str = "new"
    confidence: float = 0.7
    place: str = ""
    summary: str = ""
    analog: str = ""
    sources: int = 0
    sample: bool = False                    # dane przykładowe (makieta) — UI to oznacza


@dataclass
class Driver:
    event_id: str
    title: str
    contribution: float


@dataclass
class Bias:
    asset: str
    score: float
    label: str
    strength: str
    events_used: int
    drivers: List[Driver]


def weight(event: Event, impact: Impact, now: datetime) -> float:
    age_h = (now - event.occurred_at).total_seconds() / 3600
    if age_h < 0:
        return 0.0  # zdarzenie oczekiwane — pokazujemy, ale nie wlicza się do nastawienia
    half_life = HALF_LIFE_HOURS.get(impact.horizon, 48.0)
    decay = 0.5 ** (age_h / half_life)
    return impact.magnitude * NOVELTY_WEIGHT.get(event.novelty, 0.6) * event.confidence * decay


def aggregate(events: Sequence[Event], asset: str, now: Optional[datetime] = None, top: int = 3) -> Bias:
    now = now or datetime.now(timezone.utc)
    num, den, used = 0.0, 0.0, 0
    contribs: List[Driver] = []
    for e in events:
        imp = e.impacts.get(asset)
        if imp is None:
            continue
        w = weight(e, imp, now)
        if w <= 0:
            continue
        used += 1
        num += imp.direction * w
        den += w
        if imp.direction != 0:
            contribs.append(Driver(e.id, e.title, round(imp.direction * w, 3)))
    score = num / (den + PRIOR_WEIGHT) if den else 0.0
    score = max(-1.0, min(1.0, score))
    if abs(score) < NEUTRAL_BAND:
        label, strength = "neutral", "neutralne"
    else:
        label = "long" if score > 0 else "short"
        strength = "silne" if abs(score) >= STRONG_BAND else "umiarkowane"
    contribs.sort(key=lambda d: -abs(d.contribution))
    return Bias(asset=asset, score=round(score, 3), label=label, strength=strength,
                events_used=used, drivers=contribs[:top])


def event_to_dict(e: Event, now: Optional[datetime] = None) -> Dict[str, object]:
    now = now or datetime.now(timezone.utc)
    return {
        "id": e.id, "title": e.title, "category": e.category, "lat": e.lat, "lon": e.lon,
        "place": e.place, "summary": e.summary, "analog": e.analog, "sources": e.sources,
        "occurred_at": e.occurred_at.isoformat(),
        "age_minutes": round((now - e.occurred_at).total_seconds() / 60),
        "novelty": e.novelty, "confidence": e.confidence, "sample": e.sample,
        "impacts": {k: {"direction": v.direction, "magnitude": v.magnitude, "horizon": v.horizon}
                    for k, v in e.impacts.items()},
    }

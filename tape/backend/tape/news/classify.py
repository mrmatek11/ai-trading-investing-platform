"""Klasyfikacja klastra newsów przez Claude → Event z ocenami wpływu na XAU / XAG.

- Structured outputs (Pydantic) — zero parsowania tekstu.
- Stały system prompt z taksonomią w cache (prompt caching) — płacimy za niego raz.
- Server-side fallback przy odmowie modelu; gdy cały łańcuch odmówi → zdarzenie pomijane.
- Walidacja dowodów: cytat, którego nie ma w dostarczonych artykułach, jest odrzucany.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from .bias import Event, Impact

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """Jesteś analitykiem rynku metali szlachetnych. Dostajesz klaster artykułów o jednym zdarzeniu.
Oceń, jak to zdarzenie wpływa na cenę złota (XAU) i srebra (XAG), i przez jaki kanał.

Kanały:
- safe_haven: popyt na bezpieczną przystań (konflikty, kryzysy, niepewność)
- real_yields: realne rentowności / oczekiwania co do stóp (jastrzębio = niedźwiedzio dla złota)
- usd: siła dolara
- inflation_expectations: oczekiwania inflacyjne
- central_bank_demand: zakupy / sprzedaż złota przez banki centralne
- physical_demand: popyt fizyczny (biżuteria, sztabki, ETF)
- supply: podaż (kopalnie, strajki, rafinerie, sankcje)
- industrial_demand: popyt przemysłowy (ważny dla srebra)

Zasady:
- direction: 1 (byczo), -1 (niedźwiedzio), 0 (brak istotnego wpływu). Brak wpływu to częsta, poprawna odpowiedź.
- magnitude 1–5 względem typowych zdarzeń tego rodzaju, nie względem nagłówka.
- novelty: "already_known", jeśli artykuły opisują rzecz znaną rynkowi wcześniej.
- evidence: dosłowne cytaty z dostarczonych artykułów. Nie parafrazuj i nie wymyślaj.
- Nie dawaj rekomendacji inwestycyjnych; opisujesz mechanizm wpływu."""


class AssetImpact(BaseModel):
    direction: Literal[-1, 0, 1]
    magnitude: int = Field(ge=1, le=5)
    horizon: Literal["intraday", "days", "weeks"]
    channel: Literal["safe_haven", "real_yields", "usd", "inflation_expectations", "central_bank_demand",
                     "physical_demand", "supply", "industrial_demand", "none"]
    reasoning: str


class Evidence(BaseModel):
    quote: str
    source_index: int


class EventClassification(BaseModel):
    title: str
    category: Literal["conflict", "cb", "macro", "supply", "market", "other"]
    place: str
    lat: float
    lon: float
    xau: AssetImpact
    xag: AssetImpact
    novelty: Literal["new", "developing", "already_known"]
    confidence: float = Field(ge=0, le=1)
    summary: str
    evidence: List[Evidence]


class Article(BaseModel):
    title: str
    text: str
    url: str
    published_at: datetime


def _user_prompt(articles: Sequence[Article]) -> str:
    parts = [f"[{i}] {a.title}\n{a.url}\n{a.published_at.isoformat()}\n{a.text}" for i, a in enumerate(articles)]
    return "Artykuły w klastrze:\n\n" + "\n\n---\n\n".join(parts)


def validate_evidence(result: EventClassification, articles: Sequence[Article]) -> EventClassification:
    """Zostaw tylko cytaty, które faktycznie występują w artykule, na który wskazują."""
    kept = [e for e in result.evidence
            if 0 <= e.source_index < len(articles)
            and " ".join(e.quote.split()).lower() in " ".join(articles[e.source_index].text.split()).lower()]
    return result.model_copy(update={"evidence": kept})


def classify_cluster(client, cluster_id: str, articles: Sequence[Article]) -> Optional[Event]:
    """Sklasyfikuj klaster; zwraca None przy odmowie albo braku zweryfikowanych dowodów."""
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "medium"},
        output_format=EventClassification,
        messages=[{"role": "user", "content": _user_prompt(articles)}],
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return None
    result = validate_evidence(response.parsed_output, articles)
    if not result.evidence:
        return None  # ocena bez weryfikowalnego cytatu nie trafia do nastawienia
    occurred = min(a.published_at for a in articles)
    return Event(
        id=cluster_id, title=result.title, category=result.category if result.category != "other" else "market",
        lat=result.lat, lon=result.lon, occurred_at=occurred,
        impacts={
            "XAU": Impact(result.xau.direction, result.xau.magnitude, result.xau.horizon),
            "XAG": Impact(result.xag.direction, result.xag.magnitude, result.xag.horizon),
        },
        novelty=result.novelty, confidence=result.confidence, place=result.place,
        summary=result.summary, sources=len({a.url for a in articles}),
    )

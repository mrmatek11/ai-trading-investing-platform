"""Propozycja mapowania kolumn dla nieznanego pliku.

Dwa poziomy:
1. Heurystyka po aliasach nazw kolumn — działa zawsze, bez AI i bez kosztów.
2. Claude (structured output) — gdy heurystyka nie pokryje wymaganych pól albo plik jest nietypowy.
   Dostaje tylko nagłówki i kilka wierszy próbki. Kolumny, których nie ma w pliku, są odrzucane.
Użytkownik zawsze zatwierdza mapowanie przed importem.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from .base import norm_header
from .generic import OPTIONAL, REQUIRED

MODEL = "claude-opus-5-5"
SAMPLE_ROWS = 12

ALIASES: Dict[str, Sequence[str]] = {
    "id": ("id", "trade_id", "deal", "ticket", "position", "order_id", "transaction_id", "nr", "numer"),
    "time": ("time", "date", "datetime", "timestamp", "open_time", "execution_time", "data", "czas", "data_i_czas"),
    "symbol": ("symbol", "instrument", "ticker", "market", "asset", "walor", "papier"),
    "side": ("side", "type", "action", "direction", "buy_sell", "strona", "k_s", "typ", "operacja"),
    "qty": ("qty", "quantity", "volume", "size", "lots", "amount", "ilosc", "ilość", "wolumen", "liczba"),
    "price": ("price", "fill_price", "execution_price", "avg_price", "open_price", "cena", "kurs"),
    "fee": ("fee", "fees", "commission", "costs", "prowizja", "oplata", "opłata"),
    "pnl": ("pnl", "p_l", "profit", "realized_pnl", "gross_p_l", "zysk", "wynik"),
    "stop_loss": ("sl", "stop_loss", "stop"),
}


class FieldChoice(BaseModel):
    field: Literal["id", "time", "symbol", "side", "qty", "price", "fee", "pnl", "stop_loss", "contract_size"]
    column: Optional[str]
    confidence: float = Field(ge=0, le=1)


class MappingSuggestion(BaseModel):
    fields: List[FieldChoice]
    tz: str
    date_format: Optional[str]
    buy_values: List[str]
    sell_values: List[str]
    notes: str


def heuristic(headers: Sequence[str]) -> Dict[str, str]:
    """Pole kanoniczne → oryginalna nazwa kolumny (pierwsze trafienie aliasu)."""
    norm = {norm_header(h): h for h in headers}
    out: Dict[str, str] = {}
    used = set()
    for field in list(REQUIRED) + list(OPTIONAL):
        for alias in ALIASES.get(field, ()):
            if alias in norm and norm[alias] not in used:
                out[field] = norm[alias]
                used.add(norm[alias])
                break
    return out


def ai_suggest(client, headers: Sequence[str], rows: Sequence[Dict[str, object]],
               model: str = MODEL) -> Optional[MappingSuggestion]:
    sample = "\n".join(" | ".join(str(r.get(norm_header(h), "")) for h in headers) for r in rows[:SAMPLE_ROWS])
    prompt = (
        "Plik z historią transakcji od brokera. Dopasuj kolumny do pól: id (unikalny identyfikator "
        "transakcji), time (czas wykonania), symbol, side (kupno/sprzedaż), qty, price, fee (koszty), "
        "pnl (zrealizowany wynik), stop_loss, contract_size. Pole bez odpowiednika: column = null. "
        "Podaj strefę czasową IANA (jeśli nie wiadomo: UTC), format daty dla strptime, "
        "dokładne wartości oznaczające kupno i sprzedaż w kolumnie side.\n\n"
        f"Nagłówki:\n{' | '.join(headers)}\n\nPróbka:\n{sample}"
    )
    response = client.beta.messages.parse(
        model=model,
        max_tokens=3000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        output_format=MappingSuggestion,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output


def suggest(headers: Sequence[str], rows: Sequence[Dict[str, object]], client=None, model: str = MODEL) -> Dict[str, object]:
    columns = heuristic(headers)
    result = {"columns": columns, "source": "heuristic", "tz": "UTC", "date_format": None,
              "buy_values": None, "sell_values": None, "confidence": {k: 0.85 for k in columns}, "notes": ""}
    missing = [f for f in REQUIRED if f not in columns]
    if client is None or not missing:
        result["missing"] = missing
        return result
    ai = ai_suggest(client, headers, rows, model)
    if ai is None:
        result["missing"] = missing
        return result
    valid_headers = set(headers)
    cols, conf = {}, {}
    for choice in ai.fields:
        if choice.column and choice.column in valid_headers:   # nie ufamy nazwom spoza pliku
            cols[choice.field] = choice.column
            conf[choice.field] = choice.confidence
    result.update(columns=cols, source="ai", tz=ai.tz, date_format=ai.date_format,
                  buy_values=ai.buy_values or None, sell_values=ai.sell_values or None,
                  confidence=conf, notes=ai.notes, missing=[f for f in REQUIRED if f not in cols])
    return result

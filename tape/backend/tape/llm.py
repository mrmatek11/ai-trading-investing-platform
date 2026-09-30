"""Wspólny interfejs modeli językowych: Claude (Anthropic) albo DeepSeek.

Każda funkcja AI w Tape potrzebuje tego samego: system prompt + treść → odpowiedź zgodna ze schematem
Pydantic (albo None, gdy model odmówi / zwróci coś niezgodnego). Reszta kodu nie wie, który dostawca
odpowiada — walidacja liczb i cytatów działa identycznie dla obu.

- Anthropic: structured outputs (`messages.parse`), prompt caching, server-side fallback.
- DeepSeek: API zgodne z OpenAI (`/chat/completions`), tryb JSON; schemat dopisujemy do promptu
  i sprawdzamy odpowiedź Pydanticiem — niezgodna odpowiedź = None, nie wyjątek.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEEPSEEK_URL = "https://api.deepseek.com"
Http = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, bytes]]


class LLMError(RuntimeError):
    pass


def default_http(method: str, url: str, headers: Dict[str, str], data: Optional[bytes]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": "tape/0.1", **headers})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 — stały adres dostawcy
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class LLM:
    provider = ""

    def __init__(self, model: str):
        self.model = model

    def parse(self, system: str, user: str, schema: Type[T], max_tokens: int = 4000,
              effort: str = "medium", cache_system: bool = True) -> Optional[T]:
        raise NotImplementedError


class AnthropicLLM(LLM):
    provider = "anthropic"

    def __init__(self, client, model: str):
        super().__init__(model)
        self.client = client

    def parse(self, system, user, schema, max_tokens=4000, effort="medium", cache_system=True):
        kw = {}
        if system:
            sys_block = {"type": "text", "text": system}
            if cache_system:
                sys_block["cache_control"] = {"type": "ephemeral"}
            kw["system"] = [sys_block]
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": effort},
            output_format=schema,
            messages=[{"role": "user", "content": user}],
            **kw,
        )
        if response.stop_reason == "refusal":
            return None
        return response.parsed_output


class DeepSeekLLM(LLM):
    provider = "deepseek"

    def __init__(self, key: str, model: str, http: Http = default_http, base_url: str = DEEPSEEK_URL):
        super().__init__(model)
        self._key, self.http, self.base_url = key, http, base_url.rstrip("/")

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        status, raw = self.http(method, f"{self.base_url}{path}", {
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json", "Accept": "application/json"}, data)
        try:
            return status, json.loads(raw or b"{}")
        except ValueError:
            return status, {}

    def verify(self) -> None:
        """GET /models — nie zużywa tokenów. Rzuca LLMError z czytelnym opisem."""
        try:
            status, body = self._call("GET", "/models")
        except OSError as exc:
            raise LLMError("Nie udało się połączyć z DeepSeek — spróbuj ponownie.") from exc
        if status == 401:
            raise LLMError("DeepSeek odrzucił klucz (nieprawidłowy albo wyłączony).")
        if status != 200:
            raise LLMError(f"DeepSeek zwrócił błąd {status}.")
        ids = {m.get("id") for m in body.get("data", []) if isinstance(m, dict)}
        if ids and self.model not in ids:
            raise LLMError(f"Model {self.model} jest niedostępny dla tego klucza.")

    def parse(self, system, user, schema, max_tokens=4000, effort="medium", cache_system=True):
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        sys_text = (f"{system}\n\nOdpowiedz wyłącznie jednym obiektem JSON zgodnym z tym JSON Schema "
                    f"(bez komentarzy i bez bloku kodu):\n{schema_json}")
        body = {"model": self.model, "max_tokens": max_tokens, "stream": False,
                "messages": [{"role": "system", "content": sys_text}, {"role": "user", "content": user}]}
        if self.model != "deepseek-reasoner":            # tryb JSON — reasoner go nie obsługuje
            body["response_format"] = {"type": "json_object"}
        status, out = self._call("POST", "/chat/completions", body)
        if status != 200:
            raise LLMError(f"DeepSeek zwrócił błąd {status}: {str(out.get('error', ''))[:200]}")
        try:
            content = out["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return None
        content = content.strip()
        if content.startswith("```"):                     # na wszelki wypadek: blok ```json … ```
            content = content.strip("`").removeprefix("json").strip()
        try:
            return schema.model_validate_json(content)
        except ValidationError:
            return None


def as_llm(client_or_llm, model: str) -> LLM:
    """Klient Anthropic (także podstawiony w testach) albo gotowy LLM → LLM."""
    if isinstance(client_or_llm, LLM):
        return client_or_llm
    return AnthropicLLM(client_or_llm, model)

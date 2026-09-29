"""Własny klucz AI użytkownika: Claude (Anthropic API) albo DeepSeek.

- Klucz szyfrowany SecretBoxem (AES-256-GCM, kontekst = konto) — w bazie nigdy jawnym tekstem;
  API zwraca tylko cztery ostatnie znaki.
- Funkcje AI (przegląd journala, mapowanie kolumn) używają klucza użytkownika, a gdy go nie ma —
  klucza serwera (ANTHROPIC_API_KEY), chyba że TAPE_AI_REQUIRE_USER_KEY=1.
- Pipeline newsów działa na kluczu serwera (wspólne dane dla wszystkich).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, UtcDateTime
from .llm import LLM, AnthropicLLM, DeepSeekLLM, LLMError
from .llm import default_http as llm_default_http
from .secretbox import SecretBox

DEFAULT_MODEL = "claude-opus-5-5"
MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5 — najlepsza jakość",
    "claude-sonnet-5-5": "Claude Sonnet 5.5 — tańszy",
    "deepseek-chat": "DeepSeek V3 (chat) — najtańszy",
    "deepseek-reasoner": "DeepSeek R1 (reasoner) — rozumowanie",
}
PROVIDER = {"claude-opus-5-5": "anthropic", "claude-sonnet-5-5": "anthropic",
            "deepseek-chat": "deepseek", "deepseek-reasoner": "deepseek"}
PROVIDER_LABEL = {"anthropic": "Claude (Anthropic)", "deepseek": "DeepSeek"}
_KEY = {"anthropic": re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,300}$"),
        "deepseek": re.compile(r"^sk-[A-Za-z0-9]{20,100}$")}
_KEY_HINT = {"anthropic": "To nie wygląda na klucz Anthropic API (zaczyna się od sk-ant-).",
             "deepseek": "To nie wygląda na klucz DeepSeek (sk- i 32 znaki z platform.deepseek.com)."}


class AiCredential(Base):
    __tablename__ = "ai_credentials"

    account: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), default="anthropic")
    secret: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(4))
    model: Mapped[str] = mapped_column(String(64), default=DEFAULT_MODEL)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=lambda: datetime.now(timezone.utc))


class KeyError_(ValueError):
    pass


def context(account: str, provider: str = "anthropic") -> str:
    # kontekst szyfrowania wiąże klucz z kontem i dostawcą — skopiowany do innego wiersza się nie odszyfruje
    return f"{account}|ai|{provider}"


def provider_of(model: str) -> str:
    if model not in PROVIDER:
        raise KeyError_(f"Nieobsługiwany model: {model}")
    return PROVIDER[model]


def check_format(key: str, provider: str = "anthropic") -> str:
    key = key.strip()
    if not _KEY[provider].match(key) or (provider == "deepseek" and key.startswith("sk-ant-")):
        raise KeyError_(_KEY_HINT[provider])
    return key


def save(session: Session, box: SecretBox, account: str, key: str, model: str) -> AiCredential:
    provider = provider_of(model)
    row = session.get(AiCredential, account) or AiCredential(account=account)
    row.provider = provider
    row.secret = box.encrypt(key.encode(), context(account, provider))
    row.last4, row.model, row.updated_at = key[-4:], model, datetime.now(timezone.utc)
    session.add(row)
    return row


def load(session: Session, box: Optional[SecretBox], account: str) -> Optional[Tuple[str, str, str]]:
    """(klucz, model, dostawca) albo None."""
    row = session.get(AiCredential, account)
    if row is None or box is None:
        return None
    provider = row.provider or "anthropic"
    return box.decrypt(row.secret, context(account, provider)).decode(), row.model, provider


def require_user_key() -> bool:
    return os.getenv("TAPE_AI_REQUIRE_USER_KEY", "").strip() in ("1", "true", "yes")


def verify(client, model: str) -> None:
    """Sprawdź klucz tanio — pobranie opisu modelu nie zużywa tokenów. Rzuca KeyError_ z czytelnym opisem."""
    import anthropic

    try:
        client.models.retrieve(model)
    except anthropic.AuthenticationError as exc:
        raise KeyError_("Anthropic odrzucił klucz (nieprawidłowy albo wyłączony).") from exc
    except anthropic.PermissionDeniedError as exc:
        raise KeyError_("Klucz nie ma dostępu do tego modelu.") from exc
    except anthropic.NotFoundError as exc:
        raise KeyError_(f"Model {model} jest niedostępny dla tego klucza.") from exc
    except anthropic.APIConnectionError as exc:
        raise KeyError_("Nie udało się połączyć z Anthropic — spróbuj ponownie.") from exc


ClientFactory = Callable[[str], object]


def default_factory(key: str):
    import anthropic

    return anthropic.Anthropic(api_key=key, max_retries=2)


def make_llm(key: str, model: str, provider: str, anthropic_factory: ClientFactory = default_factory,
             http=None) -> LLM:
    if provider == "deepseek":
        return DeepSeekLLM(key, model, http or llm_default_http)
    return AnthropicLLM(anthropic_factory(key), model)


def verify_llm(model_llm: LLM) -> None:
    """Sprawdź klucz dowolnego dostawcy bez zużywania tokenów."""
    if isinstance(model_llm, DeepSeekLLM):
        try:
            model_llm.verify()
        except LLMError as exc:
            raise KeyError_(str(exc)) from exc
    else:
        verify(model_llm.client, model_llm.model)


def server_llm(anthropic_client=None) -> Optional[LLM]:
    """Klucz serwera: ANTHROPIC_API_KEY (Claude) albo DEEPSEEK_API_KEY."""
    if anthropic_client is not None:
        return AnthropicLLM(anthropic_client, os.getenv("TAPE_AI_MODEL", DEFAULT_MODEL))
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if key:
        return DeepSeekLLM(key, os.getenv("TAPE_DEEPSEEK_MODEL", "deepseek-chat"))
    return None

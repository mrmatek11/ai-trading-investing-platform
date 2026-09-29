"""Własny klucz AI użytkownika (Claude / Anthropic API).

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
from .secretbox import SecretBox

DEFAULT_MODEL = "claude-opus-5-5"
MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5 — najlepsza jakość",
    "claude-sonnet-5-5": "Claude Sonnet 5.5 — tańszy",
}
_KEY = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,300}$")


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


def context(account: str) -> str:
    return f"{account}|ai|anthropic"


def check_format(key: str) -> str:
    key = key.strip()
    if not _KEY.match(key):
        raise KeyError_("To nie wygląda na klucz Anthropic API (zaczyna się od sk-ant-).")
    return key


def save(session: Session, box: SecretBox, account: str, key: str, model: str) -> AiCredential:
    if model not in MODELS:
        raise KeyError_(f"Nieobsługiwany model: {model}")
    row = session.get(AiCredential, account) or AiCredential(account=account)
    row.secret = box.encrypt(key.encode(), context(account))
    row.last4, row.model, row.updated_at = key[-4:], model, datetime.now(timezone.utc)
    session.add(row)
    return row


def load(session: Session, box: Optional[SecretBox], account: str) -> Optional[Tuple[str, str]]:
    row = session.get(AiCredential, account)
    if row is None or box is None:
        return None
    return box.decrypt(row.secret, context(account)).decode(), row.model


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

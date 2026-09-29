"""Uwierzytelnianie: weryfikacja tokenów JWT dostawcy logowania (OIDC; np. Clerk) po kluczach JWKS.

Konto użytkownika = claim `sub` z PODPISANEGO tokenu — nigdy z parametru żądania.

Konfiguracja (zmienne środowiskowe):
  TAPE_AUTH_JWKS_URL            np. https://<twoja-instancja>.clerk.accounts.dev/.well-known/jwks.json
  TAPE_AUTH_ISSUER              oczekiwany `iss` (np. https://<twoja-instancja>.clerk.accounts.dev)
  TAPE_AUTH_AUDIENCE            opcjonalnie — oczekiwany `aud`
  TAPE_AUTH_AUTHORIZED_PARTIES  opcjonalnie — dozwolone `azp` (adresy frontendu), po przecinku
  TAPE_ADMIN_SUBS               `sub` administratorów (np. wgrywanie cen), po przecinku

Bez TAPE_AUTH_JWKS_URL aplikacja działa w trybie jednego użytkownika (konto „default”).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import jwt

Verifier = Callable[[str], str]


class AuthError(Exception):
    pass


@dataclass
class JwksVerifier:
    jwks_url: str
    issuer: str
    audience: Optional[str] = None
    authorized_parties: Sequence[str] = field(default_factory=tuple)
    leeway_seconds: int = 10
    _client: Optional[jwt.PyJWKClient] = None

    def signing_key(self, token: str):
        if self._client is None:
            self._client = jwt.PyJWKClient(self.jwks_url, cache_keys=True, lifespan=3600)
        return self._client.get_signing_key_from_jwt(token).key

    def __call__(self, token: str) -> str:
        try:
            claims = jwt.decode(
                token,
                self.signing_key(token),
                algorithms=["RS256", "ES256"],          # nigdy "none" ani HS* z kluczem publicznym
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": self.audience is not None},
                leeway=self.leeway_seconds,
            )
        except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
            raise AuthError(str(exc)) from exc
        if self.authorized_parties and claims.get("azp") not in self.authorized_parties:
            raise AuthError("niedozwolony azp")
        sub = claims["sub"]
        if not isinstance(sub, str) or not sub or len(sub) > 64:
            raise AuthError("niepoprawny sub")
        return sub


def _csv(name: str) -> tuple:
    return tuple(x.strip() for x in os.getenv(name, "").split(",") if x.strip())


def verifier_from_env() -> Optional[JwksVerifier]:
    url = os.getenv("TAPE_AUTH_JWKS_URL", "")
    if not url:
        return None
    issuer = os.getenv("TAPE_AUTH_ISSUER", "")
    if not issuer:
        raise RuntimeError("TAPE_AUTH_ISSUER jest wymagany razem z TAPE_AUTH_JWKS_URL")
    return JwksVerifier(url, issuer, os.getenv("TAPE_AUTH_AUDIENCE") or None, _csv("TAPE_AUTH_AUTHORIZED_PARTIES"))


def admin_subs() -> tuple:
    return _csv("TAPE_ADMIN_SUBS")

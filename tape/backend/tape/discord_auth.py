"""Logowanie przez Discord (OAuth2, authorization code) z własną sesją Tape w ciasteczku.

Przepływ:
  /api/auth/discord/login     → losowy `state` w ciasteczku HttpOnly (10 min) → przekierowanie do Discorda
  /api/auth/discord/callback  → `state` z adresu == `state` z ciasteczka → wymiana `code` na token (po stronie
                                serwera, z client_secret) → /users/@me → sesja Tape (JWT HS256) w ciasteczku
                                HttpOnly + Secure + SameSite=Lax; tokenu Discorda NIE przechowujemy
  /api/auth/logout            → usunięcie ciasteczka

Konto = "discord:<id>" (id Discorda jest stałe, nazwa użytkownika może się zmienić).
Żądania zmieniające dane z sesją w ciasteczku muszą mieć nagłówek Origin zgodny z TAPE_APP_URL (CSRF).

Konfiguracja:
  TAPE_DISCORD_CLIENT_ID, TAPE_DISCORD_CLIENT_SECRET   — Discord Developer Portal → OAuth2
  TAPE_APP_URL          — publiczny adres aplikacji, np. https://app.tape.example
                          (Redirect w Discordzie: https://app.tape.example/api/auth/discord/callback)
  TAPE_SESSION_SECRET   — losowy sekret ≥ 32 znaki do podpisu sesji
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

import jwt

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
ME_URL = "https://discord.com/api/users/@me"
SESSION_COOKIE = "tape_session"
STATE_COOKIE = "tape_oauth_state"
SESSION_TTL = 30 * 24 * 3600
ISSUER, AUDIENCE = "tape", "tape-session"

Http = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, bytes]]


class DiscordAuthError(Exception):
    pass


def default_http(method: str, url: str, headers: Dict[str, str], data: Optional[bytes]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": "tape/0.1", **headers})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — stałe adresy Discorda
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


@dataclass
class DiscordConfig:
    client_id: str
    client_secret: str
    app_url: str
    session_secret: str

    @classmethod
    def from_env(cls) -> Optional["DiscordConfig"]:
        cid = os.getenv("TAPE_DISCORD_CLIENT_ID", "").strip()
        if not cid:
            return None
        cfg = cls(cid, os.getenv("TAPE_DISCORD_CLIENT_SECRET", "").strip(),
                  os.getenv("TAPE_APP_URL", "").strip().rstrip("/"), os.getenv("TAPE_SESSION_SECRET", ""))
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.client_secret:
            raise RuntimeError("TAPE_DISCORD_CLIENT_SECRET jest wymagany razem z TAPE_DISCORD_CLIENT_ID")
        if not self.app_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise RuntimeError("TAPE_APP_URL musi być adresem https:// (albo localhost w developmencie)")
        if len(self.session_secret) < 32:
            raise RuntimeError("TAPE_SESSION_SECRET musi mieć co najmniej 32 znaki")

    @property
    def redirect_uri(self) -> str:
        return f"{self.app_url}/api/auth/discord/callback"

    @property
    def secure_cookies(self) -> bool:
        return self.app_url.startswith("https://")

    @property
    def origin(self) -> str:
        p = urllib.parse.urlsplit(self.app_url)
        return f"{p.scheme}://{p.netloc}"


def new_state() -> str:
    return secrets.token_urlsafe(32)


def authorize_url(cfg: DiscordConfig, state: str) -> str:
    q = urllib.parse.urlencode({"client_id": cfg.client_id, "redirect_uri": cfg.redirect_uri,
                                "response_type": "code", "scope": "identify", "state": state, "prompt": "none"})
    return f"{AUTHORIZE_URL}?{q}"


def exchange(cfg: DiscordConfig, code: str, http: Http = default_http) -> Dict[str, object]:
    """code → token → profil użytkownika. Rzuca DiscordAuthError z opisem bez sekretów."""
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code,
                                   "redirect_uri": cfg.redirect_uri,
                                   "client_id": cfg.client_id, "client_secret": cfg.client_secret}).encode()
    status, raw = http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded",
                                           "Accept": "application/json"}, body)
    try:
        tok = json.loads(raw or b"{}")
    except ValueError:
        tok = {}
    if status != 200 or "access_token" not in tok:
        raise DiscordAuthError(f"Discord odrzucił logowanie ({tok.get('error', status)})")
    status, raw = http("GET", ME_URL, {"Authorization": f"Bearer {tok['access_token']}", "Accept": "application/json"}, None)
    try:
        me = json.loads(raw or b"{}")
    except ValueError:
        me = {}
    if status != 200 or not str(me.get("id", "")).isdigit():
        raise DiscordAuthError("Nie udało się pobrać profilu Discord")
    return me


def avatar_url(me: Dict[str, object]) -> Optional[str]:
    if me.get("avatar"):
        return f"https://cdn.discordapp.com/avatars/{me['id']}/{me['avatar']}.png?size=64"
    return None


class Sessions:
    def __init__(self, secret: str, ttl: int = SESSION_TTL):
        self.secret, self.ttl = secret, ttl

    def issue(self, sub: str, name: str, avatar: Optional[str], now: Optional[int] = None) -> str:
        now = now or int(time.time())
        return jwt.encode({"sub": sub, "name": name[:64], "avatar": avatar, "iat": now, "exp": now + self.ttl,
                           "iss": ISSUER, "aud": AUDIENCE, "jti": secrets.token_hex(8)}, self.secret, algorithm="HS256")

    def verify(self, token: str) -> Dict[str, object]:
        try:
            claims = jwt.decode(token, self.secret, algorithms=["HS256"], issuer=ISSUER, audience=AUDIENCE,
                                options={"require": ["exp", "iat", "sub", "iss", "aud"]})
        except jwt.PyJWTError as exc:
            raise DiscordAuthError("sesja nieważna") from exc
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.startswith("discord:") or len(sub) > 64:
            raise DiscordAuthError("sesja nieważna")
        return claims

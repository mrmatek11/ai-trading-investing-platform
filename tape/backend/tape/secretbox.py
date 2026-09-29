"""Szyfrowanie danych dostępowych do brokerów (envelope encryption, AES-256-GCM).

Każdy sekret dostaje własny losowy klucz danych (DEK), którym szyfrujemy treść; DEK szyfrujemy kluczem
głównym (KEK) z TAPE_SECRET_KEYS. Rotacja: dopisz nowy klucz na początku listy — nowe sekrety idą nowym
kluczem, stare dalej się odszyfrują, `rewrap` przepina je bez odszyfrowywania treści.

TAPE_SECRET_KEYS="k2:<base64 32 bajtów>,k1:<base64 32 bajtów>"   (pierwszy = aktywny)
Wygeneruj klucz:  python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"

`context` (np. konto + id połączenia) jest wiązany jako AAD — szyfrogramu nie da się przenieść
do innego konta ani połączenia, nawet mając dostęp zapisu do bazy.
"""

from __future__ import annotations

import base64
import os
from typing import Dict, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "v1"


class SecretError(Exception):
    pass


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class SecretBox:
    def __init__(self, keys: Dict[str, bytes], active: str):
        if active not in keys:
            raise SecretError("aktywny klucz nie istnieje")
        for kid, key in keys.items():
            if len(key) != 32:
                raise SecretError(f"klucz {kid} musi mieć 32 bajty (AES-256)")
            if not kid or "." in kid or ":" in kid:
                raise SecretError(f"niepoprawny identyfikator klucza: {kid!r}")
        self.keys, self.active = keys, active

    @classmethod
    def from_env(cls, value: Optional[str] = None) -> Optional["SecretBox"]:
        raw = (value if value is not None else os.getenv("TAPE_SECRET_KEYS", "")).strip()
        if not raw:
            return None
        keys: Dict[str, bytes] = {}
        order = []
        for part in raw.split(","):
            kid, _, b64 = part.strip().partition(":")
            if not b64:
                raise SecretError("format TAPE_SECRET_KEYS: id:base64,id:base64")
            keys[kid] = base64.b64decode(b64)
            order.append(kid)
        return cls(keys, order[0])

    def encrypt(self, plaintext: bytes, context: str) -> str:
        dek = AESGCM.generate_key(bit_length=256)
        n1, n2 = os.urandom(12), os.urandom(12)
        body = AESGCM(dek).encrypt(n1, plaintext, context.encode())
        wrapped = AESGCM(self.keys[self.active]).encrypt(n2, dek, f"dek|{context}".encode())
        return ".".join((VERSION, self.active, _b64e(n2 + wrapped), _b64e(n1 + body)))

    def _unwrap(self, token: str, context: str):
        try:
            version, kid, wrapped, body = token.split(".")
        except ValueError as exc:
            raise SecretError("uszkodzony szyfrogram") from exc
        if version != VERSION or kid not in self.keys:
            raise SecretError(f"brak klucza {kid!r} — nie usuwaj starych kluczy przed rotacją")
        w = _b64d(wrapped)
        try:
            dek = AESGCM(self.keys[kid]).decrypt(w[:12], w[12:], f"dek|{context}".encode())
        except InvalidTag as exc:
            raise SecretError("szyfrogram nie pasuje do klucza lub kontekstu") from exc
        return dek, body

    def decrypt(self, token: str, context: str) -> bytes:
        dek, body = self._unwrap(token, context)
        b = _b64d(body)
        try:
            return AESGCM(dek).decrypt(b[:12], b[12:], context.encode())
        except InvalidTag as exc:
            raise SecretError("szyfrogram nie pasuje do klucza lub kontekstu") from exc

    def rewrap(self, token: str, context: str) -> str:
        """Przepnij DEK na aktywny klucz główny (rotacja) — treść sekretu zostaje nietknięta."""
        dek, body = self._unwrap(token, context)
        n2 = os.urandom(12)
        wrapped = AESGCM(self.keys[self.active]).encrypt(n2, dek, f"dek|{context}".encode())
        return ".".join((VERSION, self.active, _b64e(n2 + wrapped), body))

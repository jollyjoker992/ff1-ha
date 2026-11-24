"""DP-1 signatures (spec §7.1.1), the way dp1-js and ff-cli make them, so their verifiers accept ours.

    digest       = SHA-256( JCS(playlist without `signature` and `signatures`) + "\\n" )
    payload_hash = "sha256:" + hex(digest)
    sig          = base64url( Ed25519-sign(digest) ), no padding
    kid          = "did:key:z" + base58btc( 0xed 0x01 + public key )

The key is an Ed25519 private key kept as base64 PKCS#8 DER, the form `ff-cli setup` writes, in a file only
its owner can read. Signing needs the `cryptography` package; everything else here is the standard library.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ROLES = ("curator", "agent", "feed", "institution", "licensor")


class SigningError(Exception):
    pass


# ---------- JCS (RFC 8785) ----------
def _number(n: int | float) -> str:
    if isinstance(n, bool):
        raise SigningError("not a number")
    if isinstance(n, int):
        if abs(n) >= 2 ** 53:
            raise SigningError("an integer beyond 2^53 cannot be canonical: write it as a string")
        return str(n)
    if n != n or n in (float("inf"), float("-inf")):
        raise SigningError("NaN and Infinity have no canonical form")
    if n == int(n) and abs(n) < 1e21:
        return str(int(n))
    r = repr(n)                                    # shortest round-trip, as ECMAScript; fix the exponent's shape
    if "e" in r:
        mant, exp = r.split("e")
        r = f"{mant}e{'+' if int(exp) >= 0 else '-'}{abs(int(exp))}"
    return r


def canonical(value: Any) -> str:
    """The JSON Canonicalization Scheme: sorted keys (by UTF-16 code units), no spaces, minimal escapes."""
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise SigningError("an object's names are strings")
        keys = sorted(value, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical(value[k]) for k in keys) + "}"
    raise SigningError(f"{type(value).__name__} is not JSON")


def digest(playlist: dict[str, Any]) -> bytes:
    body = {k: v for k, v in playlist.items() if k not in ("signature", "signatures")}
    return hashlib.sha256(canonical(body).encode("utf-8") + b"\n").digest()


def payload_hash(playlist: dict[str, Any]) -> str:
    return "sha256:" + digest(playlist).hex()


# ---------- did:key ----------
def _b58encode(data: bytes) -> str:
    n, out = int.from_bytes(data, "big"), ""
    while n:
        n, rem = divmod(n, 58)
        out = B58[rem] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def _b58decode(text: str) -> bytes:
    n = 0
    for c in text:
        if c not in B58:
            raise SigningError("not base58btc")
        n = n * 58 + B58.index(c)
    pad = len(text) - len(text.lstrip("1"))
    return b"\0" * pad + n.to_bytes((n.bit_length() + 7) // 8, "big")


def did_key(public: bytes) -> str:
    if len(public) != 32:
        raise SigningError("an Ed25519 public key is 32 bytes")
    return "did:key:z" + _b58encode(b"\xed\x01" + public)


def public_of(kid: str) -> bytes:
    if not kid.startswith("did:key:z"):
        raise SigningError("only did:key with base58btc is read here")
    data = _b58decode(kid[len("did:key:z"):])
    if len(data) != 34 or data[:2] != b"\xed\x01":
        raise SigningError("the did:key is not an Ed25519 key")
    return data[2:]


# ---------- the key ----------
def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    except ImportError as e:                          # pragma: no cover - depends on the environment
        raise SigningError("signing needs the 'cryptography' package") from e
    return serialization, Ed25519PrivateKey, Ed25519PublicKey


class Key:
    """An Ed25519 signing key. `Key.at(path)` reads it, or makes it the first time (mode 0600)."""

    def __init__(self, private) -> None:
        self._private = private

    @classmethod
    def generate(cls) -> Key:
        return cls(_crypto()[1].generate())

    @classmethod
    def parse(cls, text: str) -> Key:
        """base64 PKCS#8 DER (what ff-cli writes), or a 32-byte seed as hex or base64."""
        serialization, Private, _ = _crypto()
        text = text.strip()
        try:
            raw = bytes.fromhex(text[2:] if text.startswith("0x") else text) if re.fullmatch(r"(0x)?[0-9a-fA-F]+", text) \
                else base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_")
        except ValueError as e:
            raise SigningError("the key is neither hex nor base64") from e
        try:
            return cls(Private.from_private_bytes(raw) if len(raw) == 32 else serialization.load_der_private_key(raw, None))
        except (ValueError, TypeError) as e:
            raise SigningError("the key is not an Ed25519 private key") from e

    @classmethod
    def at(cls, path: str | Path) -> Key:
        path = Path(path)
        if path.exists():
            return cls.parse(path.read_text())
        key = cls.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key.export() + "\n")
        return key

    def export(self) -> str:
        s = _crypto()[0]
        return base64.b64encode(self._private.private_bytes(s.Encoding.DER, s.PrivateFormat.PKCS8, s.NoEncryption())).decode()

    @property
    def did(self) -> str:
        s = _crypto()[0]
        return did_key(self._private.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw))

    def sign(self, playlist: dict[str, Any], role: str = "curator", now: datetime | None = None) -> dict[str, Any]:
        """The playlist with one more entry in `signatures`. A document changed after signing must be signed anew."""
        if role not in ROLES:
            raise SigningError(f"role is one of {', '.join(ROLES)}")
        d = digest(playlist)
        entry = {"alg": "ed25519", "kid": self.did,
                 "ts": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "payload_hash": "sha256:" + d.hex(), "role": role,
                 "sig": base64.urlsafe_b64encode(self._private.sign(d)).rstrip(b"=").decode()}
        return {**playlist, "signatures": [*playlist.get("signatures", []), entry]}


def verify(playlist: dict[str, Any]) -> list[dict[str, str]]:
    """Each signature's verdict: `valid`, `invalid` (with why), or `unchecked` (an algorithm not read here)."""
    _, _, Public = _crypto()
    want, d, out = payload_hash(playlist), digest(playlist), []
    for s in playlist.get("signatures") or []:
        verdict, why = "invalid", ""
        if not isinstance(s, dict):
            why = "not an object"
        elif s.get("alg") != "ed25519":
            verdict, why = "unchecked", f"alg {s.get('alg')}"
        elif s.get("payload_hash") != want:
            why = "payload_hash is not this document's"
        else:
            try:
                sig = base64.urlsafe_b64decode(s.get("sig", "") + "=" * (-len(s.get("sig", "")) % 4))
                Public.from_public_bytes(public_of(str(s.get("kid")))).verify(sig, d)
                verdict = "valid"
            except Exception as e:  # noqa: BLE001 - any failure is "invalid"
                why = type(e).__name__
        out.append({"role": str(s.get("role")) if isinstance(s, dict) else "", "kid": str(s.get("kid")) if isinstance(s, dict) else "",
                    "verdict": verdict, "why": why})
    return out

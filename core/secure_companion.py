"""Zero-trust protocol primitives for the ConnectPhone Android companion.

The companion transport is deliberately separate from ADB.  Every phone is
enrolled explicitly, receives a unique key, and must authenticate each message.
This module contains no network listener and grants no Android permissions; it
only defines the security boundary used by both sides of that future transport.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core import keychain


PROTOCOL_VERSION = 1
PAIRING_TTL_SECONDS = 120
MESSAGE_TTL_SECONDS = 30
MAX_CLOCK_SKEW_SECONDS = 10
MAX_MESSAGE_BYTES = 64 * 1024

# Companion mode never becomes a route around Android's lock screen or consent
# dialogs.  Privileged features are intentionally absent from this allowlist.
COMPANION_CAPABILITIES = frozenset(
    {
        "device.status",
        "alert.start",
        "alert.stop",
    }
)

_B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class CompanionSecurityError(ValueError):
    """Raised when authentication, freshness, or authorization fails."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 512 or not _B64_RE.fullmatch(value):
        raise CompanionSecurityError("Invalid encoded value")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise CompanionSecurityError("Invalid encoded value") from exc


def _canonical(value: dict) -> bytes:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CompanionSecurityError("Message is not JSON serializable") from exc
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise CompanionSecurityError("Message exceeds protocol size limit")
    return encoded


def _hkdf(secret: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF-SHA256 with domain separation."""
    extracted = hmac.new(salt, secret, hashlib.sha256).digest()
    output = b""
    block = b""
    counter = 1
    while len(output) < length:
        block = hmac.new(extracted, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
        counter += 1
    return output[:length]


def _device_key_name(device_id: str) -> str:
    digest = hashlib.sha256(device_id.encode("utf-8")).hexdigest()
    return f"companion.device.{digest}"


class CompanionKeyVault:
    """Per-device secrets backed by macOS Keychain."""

    def store(self, device_id: str, secret: bytes) -> None:
        if not device_id or len(device_id) > 256 or len(secret) != 32:
            raise CompanionSecurityError("Invalid device identity or secret")
        keychain.set(_device_key_name(device_id), _b64(secret))

    def load(self, device_id: str) -> bytes | None:
        value = keychain.get(_device_key_name(device_id))
        if not value:
            return None
        secret = _unb64(value)
        if len(secret) != 32:
            raise CompanionSecurityError("Stored device key is invalid")
        return secret

    def revoke(self, device_id: str) -> None:
        keychain.delete(_device_key_name(device_id))


@dataclass(frozen=True)
class PairingOffer:
    session_id: str
    secret: bytes
    expires_at: int

    @classmethod
    def create(cls, now: int | None = None) -> "PairingOffer":
        issued = int(time.time() if now is None else now)
        return cls(_b64(secrets.token_bytes(18)), secrets.token_bytes(32), issued + PAIRING_TTL_SECONDS)

    def qr_payload(self, host: str | None = None, port: int | None = None) -> str:
        data = {
            "v": PROTOCOL_VERSION,
            "sid": self.session_id,
            "secret": _b64(self.secret),
            "exp": self.expires_at,
        }
        if host is not None and port is not None:
            data.update(host=host, port=int(port))
        return "CP1:" + _b64(
            _canonical(data)
        )

    def phone_proof(self, device_id: str, phone_nonce: str) -> str:
        material = f"phone-proof\0{self.session_id}\0{device_id}\0{phone_nonce}".encode()
        return _b64(hmac.new(self.secret, material, hashlib.sha256).digest())

    def mac_proof(self, device_id: str, phone_nonce: str) -> str:
        material = f"mac-proof\0{self.session_id}\0{device_id}\0{phone_nonce}".encode()
        return _b64(hmac.new(self.secret, material, hashlib.sha256).digest())

    def complete(
        self,
        device_id: str,
        phone_nonce: str,
        proof: str,
        vault: CompanionKeyVault,
        now: int | None = None,
    ) -> bytes:
        current = int(time.time() if now is None else now)
        if current > self.expires_at:
            raise CompanionSecurityError("Pairing offer expired")
        try:
            nonce_bytes = _unb64(phone_nonce)
        except CompanionSecurityError as exc:
            raise CompanionSecurityError("Invalid phone nonce") from exc
        if len(nonce_bytes) < 18 or len(nonce_bytes) > 64:
            raise CompanionSecurityError("Invalid phone nonce")
        expected = self.phone_proof(device_id, phone_nonce)
        if not hmac.compare_digest(expected, proof):
            raise CompanionSecurityError("Phone pairing proof rejected")
        device_secret = _hkdf(
            self.secret,
            hashlib.sha256(phone_nonce.encode()).digest(),
            f"ConnectPhone companion v1\0{device_id}".encode(),
        )
        vault.store(device_id, device_secret)
        return device_secret


class SecureChannel:
    """Authenticate commands and reject stale, reordered, or replayed data."""

    def __init__(
        self,
        device_id: str,
        secret: bytes,
        capabilities: Iterable[str] = COMPANION_CAPABILITIES,
        clock: Callable[[], float] = time.time,
    ):
        if not device_id or len(secret) != 32:
            raise CompanionSecurityError("Invalid secure-channel identity")
        self.device_id = device_id
        self._key = _hkdf(secret, b"ConnectPhone-v1", b"authenticated-command")
        self._encryption_key = _hkdf(secret, b"ConnectPhone-v1", b"encrypted-payload")
        self._capabilities = frozenset(capabilities) & COMPANION_CAPABILITIES
        self._clock = clock
        self._send_sequence = 0
        self._receive_sequence = 0
        self._seen_nonces: set[str] = set()
        self._lock = threading.Lock()

    def seal(self, capability: str, payload: dict) -> dict:
        if capability not in self._capabilities:
            raise CompanionSecurityError("Capability is not authorized")
        if not isinstance(payload, dict):
            raise CompanionSecurityError("Payload must be an object")
        with self._lock:
            self._send_sequence += 1
            header = {
                "v": PROTOCOL_VERSION,
                "device": self.device_id,
                "cap": capability,
                "iat": int(self._clock()),
                "seq": self._send_sequence,
                "nonce": _b64(secrets.token_bytes(18)),
            }
            iv = secrets.token_bytes(12)
            ciphertext = AESGCM(self._encryption_key).encrypt(iv, _canonical(payload), _canonical(header))
            body = {**header, "iv": _b64(iv), "ciphertext": _b64(ciphertext)}
            return {**body, "mac": _b64(hmac.new(self._key, _canonical(body), hashlib.sha256).digest())}

    def open(self, message: dict) -> tuple[str, dict]:
        if not isinstance(message, dict):
            raise CompanionSecurityError("Invalid message")
        required = {"v", "device", "cap", "iat", "seq", "nonce", "iv", "ciphertext", "mac"}
        if set(message) != required:
            raise CompanionSecurityError("Unexpected message fields")
        body = {key: message[key] for key in message if key != "mac"}
        expected = _b64(hmac.new(self._key, _canonical(body), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, str(message["mac"])):
            raise CompanionSecurityError("Message authentication failed")
        if message["v"] != PROTOCOL_VERSION or message["device"] != self.device_id:
            raise CompanionSecurityError("Protocol or device mismatch")
        capability = message["cap"]
        if not isinstance(capability, str):
            raise CompanionSecurityError("Invalid capability")
        if capability not in self._capabilities:
            raise CompanionSecurityError("Capability is not authorized")
        issued = message["iat"]
        sequence = message["seq"]
        if isinstance(issued, bool) or not isinstance(issued, int) or isinstance(sequence, bool) or not isinstance(sequence, int):
            raise CompanionSecurityError("Invalid freshness metadata")
        if sequence < 1:
            raise CompanionSecurityError("Invalid freshness metadata")
        now = int(self._clock())
        if issued > now + MAX_CLOCK_SKEW_SECONDS or now - issued > MESSAGE_TTL_SECONDS:
            raise CompanionSecurityError("Message is outside the freshness window")
        nonce = message["nonce"]
        if not isinstance(nonce, str) or len(_unb64(nonce)) < 16:
            raise CompanionSecurityError("Invalid nonce")
        header = {key: body[key] for key in ("v", "device", "cap", "iat", "seq", "nonce")}
        try:
            iv = _unb64(str(message["iv"]))
            if len(iv) != 12:
                raise CompanionSecurityError("Invalid encryption nonce")
            clear = AESGCM(self._encryption_key).decrypt(
                iv,
                _unb64(str(message["ciphertext"])),
                _canonical(header),
            )
            payload = json.loads(clear)
        except (ValueError, TypeError, json.JSONDecodeError, CompanionSecurityError) as exc:
            raise CompanionSecurityError("Payload decryption failed") from exc
        if not isinstance(payload, dict):
            raise CompanionSecurityError("Payload must be an object")
        with self._lock:
            if sequence <= self._receive_sequence or nonce in self._seen_nonces:
                raise CompanionSecurityError("Replay or reordered message rejected")
            self._receive_sequence = sequence
            self._seen_nonces.add(nonce)
            # Sequence ordering makes older nonces irrelevant; bound memory in
            # case a peer keeps one channel alive for an unusually long time.
            if len(self._seen_nonces) > 4096:
                self._seen_nonces = {nonce}
        return capability, payload

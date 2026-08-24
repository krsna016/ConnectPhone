"""Authenticated local-network server for non-ADB Android companions."""

from __future__ import annotations

import json
import logging
import re
import socket
import socketserver
import threading
import time
from dataclasses import dataclass

from core.secure_companion import (
    CompanionKeyVault,
    CompanionSecurityError,
    PairingOffer,
    SecureChannel,
)


COMPANION_PORT = 8377
MAX_LINE = 64 * 1024
MAX_CONNECTIONS = 32
MAX_PAIRING_OFFERS = 8
PAIRING_STATUS_TTL = 10 * 60
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def local_ipv4() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return str(probe.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        probe.close()


@dataclass
class CompanionDevice:
    device_id: str
    model: str
    address: str
    connected_at: float
    last_seen: float
    sender: SecureChannel
    connection: socket.socket
    pairing_id: str | None = None
    send_lock: threading.Lock | None = None


class _CompanionTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = MAX_CONNECTIONS

    def __init__(self, *args, **kwargs):
        self._connection_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def verify_request(self, request, client_address):
        return self._connection_slots.acquire(blocking=False)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()


class CompanionServer:
    # LAN phones must reach this listener. The exposed socket accepts only the
    # bounded, mutually authenticated encrypted protocol implemented below.
    def __init__(self, host="0.0.0.0", port=COMPANION_PORT, vault=None):  # noqa: S104  # nosec B104
        self.host = host
        self.port = int(port)
        self.vault = vault or CompanionKeyVault()
        self.logger = logging.getLogger(__name__)
        self._offers: dict[str, PairingOffer] = {}
        self._paired_sessions: dict[str, dict] = {}
        self._devices: dict[str, CompanionDevice] = {}
        self._lock = threading.RLock()
        self._server = None
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        manager = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                manager._handle(self)

        self._server = _CompanionTCPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, name="ConnectPhone-Companion", daemon=True)
        self._thread.start()

    def stop(self):
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server:
            server.shutdown()
            server.server_close()
        with self._lock:
            for device in self._devices.values():
                try:
                    device.connection.close()
                except OSError:
                    pass
            self._devices.clear()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)

    def new_pairing(self) -> tuple[PairingOffer, str]:
        offer = PairingOffer.create()
        with self._lock:
            self._offers[offer.session_id] = offer
            self._prune_offers()
            while len(self._offers) > MAX_PAIRING_OFFERS:
                self._offers.pop(next(iter(self._offers)))
        return offer, offer.qr_payload(local_ipv4(), self.port)

    def pairing_status(self, session_id: str) -> dict:
        with self._lock:
            self._prune_offers()
            offer = self._offers.get(session_id)
            paired = self._paired_sessions.get(session_id)
        if paired:
            return {"state": "paired", "device_id": paired["device_id"], "model": paired["model"]}
        if offer and offer.expires_at >= int(time.time()):
            return {"state": "waiting", "expires_at": offer.expires_at}
        return {"state": "expired"}

    def devices(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "device_id": item.device_id,
                    "model": item.model,
                    "address": item.address,
                    "status": "online",
                    "last_seen": item.last_seen,
                    "connection": "companion",
                }
                for item in self._devices.values()
            ]

    def send(self, device_id: str, capability: str, payload: dict) -> None:
        with self._lock:
            device = self._devices.get(device_id)
            if not device:
                raise CompanionSecurityError("Companion phone is offline")
            message = device.sender.seal(capability, payload)
            data = json.dumps(message, separators=(",", ":")).encode() + b"\n"
            try:
                with device.send_lock or self._lock:
                    device.connection.sendall(data)
            except OSError as exc:
                self._devices.pop(device_id, None)
                raise CompanionSecurityError("Companion connection was lost") from exc

    def alert(self, device_id: str, enabled: bool) -> None:
        self.send(device_id, "alert.start" if enabled else "alert.stop", {"pattern": "siren"} if enabled else {})

    def revoke(self, device_id: str) -> None:
        self.vault.revoke(device_id)
        with self._lock:
            device = self._devices.pop(device_id, None)
        if device:
            try:
                device.connection.close()
            except OSError:
                pass

    def _prune_offers(self):
        now = int(time.time())
        self._offers = {key: value for key, value in self._offers.items() if value.expires_at >= now}
        self._paired_sessions = {
            key: value for key, value in self._paired_sessions.items()
            if int(value.get("paired_at", 0)) + PAIRING_STATUS_TTL >= now
        }

    @staticmethod
    def _read_json(handler) -> dict:
        raw = handler.rfile.readline(MAX_LINE + 1)
        if not raw or len(raw) > MAX_LINE or not raw.endswith(b"\n"):
            raise CompanionSecurityError("Invalid companion frame")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanionSecurityError("Invalid companion JSON") from exc
        if not isinstance(value, dict):
            raise CompanionSecurityError("Companion frame must be an object")
        return value

    def _write_json(self, handler, value):
        handler.wfile.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
        handler.wfile.flush()

    def _handle(self, handler):
        device_id = None
        try:
            handler.request.settimeout(10)
            handler.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            hello = self._read_json(handler)
            if hello.get("type") == "pair":
                device_id, secret, pairing_id = self._accept_pairing(handler, hello)
            elif hello.get("type") == "reconnect":
                device_id = str(hello.get("device", ""))
                if not _DEVICE_ID_RE.fullmatch(device_id):
                    raise CompanionSecurityError("Invalid device identity")
                secret = self.vault.load(device_id)
                pairing_id = None
                if not secret:
                    raise CompanionSecurityError("Device is not enrolled")
                self._write_json(handler, {"type": "ready", "v": 1})
            else:
                raise CompanionSecurityError("Unknown companion hello")

            receiver = SecureChannel(device_id, secret)
            sender = SecureChannel(device_id, secret)
            first = self._read_json(handler)
            capability, payload = receiver.open(first)
            if capability != "device.status":
                raise CompanionSecurityError("First command must identify device status")
            device = CompanionDevice(
                device_id=device_id,
                model=str(payload.get("model") or hello.get("model") or "Android")[:120],
                address=str(handler.client_address[0]),
                connected_at=time.time(),
                last_seen=time.time(),
                sender=sender,
                connection=handler.request,
                pairing_id=pairing_id,
                send_lock=threading.Lock(),
            )
            with self._lock:
                old = self._devices.get(device_id)
                self._devices[device_id] = device
            if old and old.connection is not handler.request:
                try:
                    old.connection.close()
                except OSError:
                    pass
            handler.request.settimeout(90)
            while True:
                message = self._read_json(handler)
                capability, _ = receiver.open(message)
                if capability != "device.status":
                    raise CompanionSecurityError("Phone command is not permitted")
                with self._lock:
                    current = self._devices.get(device_id)
                    if current and current.connection is handler.request:
                        current.last_seen = time.time()
        except (OSError, CompanionSecurityError) as exc:
            self.logger.info("Companion connection closed: %s", exc)
        finally:
            if device_id:
                with self._lock:
                    current = self._devices.get(device_id)
                    if current and current.connection is handler.request:
                        self._devices.pop(device_id, None)

    def _accept_pairing(self, handler, hello):
        session_id = str(hello.get("sid", ""))
        device_id = str(hello.get("device", ""))
        phone_nonce = str(hello.get("phone_nonce", ""))
        proof = str(hello.get("proof", ""))
        if not _DEVICE_ID_RE.fullmatch(device_id) or len(phone_nonce) > 256:
            raise CompanionSecurityError("Invalid pairing identity")
        with self._lock:
            offer = self._offers.pop(session_id, None)
        if not offer:
            raise CompanionSecurityError("Pairing offer is missing or already used")
        secret = offer.complete(device_id, phone_nonce, proof, self.vault)
        with self._lock:
            self._paired_sessions[session_id] = {
                "device_id": device_id,
                "model": str(hello.get("model") or "Android")[:120],
                "paired_at": int(time.time()),
            }
        self._write_json(handler, {"type": "paired", "v": 1, "proof": offer.mac_proof(device_id, phone_nonce)})
        return device_id, secret, session_id

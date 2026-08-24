import json
import base64
import socket
import threading
import unittest

from core.companion_server import CompanionServer
from core.secure_companion import SecureChannel


class MemoryVault:
    def __init__(self):
        self.values = {}
    def store(self, device_id, secret): self.values[device_id] = secret
    def load(self, device_id): return self.values.get(device_id)
    def revoke(self, device_id): self.values.pop(device_id, None)


class CompanionServerTests(unittest.TestCase):
    def setUp(self):
        self.vault = MemoryVault()
        self.server = CompanionServer(host="127.0.0.1", port=0, vault=self.vault)
        self.server.start()

    def tearDown(self):
        self.server.stop()

    def test_pair_status_command_and_replay_protection(self):
        offer, _ = self.server.new_pairing()
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=2)
        stream = sock.makefile("rwb")
        nonce = base64.urlsafe_b64encode(b"n" * 24).rstrip(b"=").decode()
        hello = {"type":"pair", "sid":offer.session_id, "device":"phone-1", "phone_nonce":nonce,
                 "proof":offer.phone_proof("phone-1", nonce), "model":"Test Phone"}
        stream.write(json.dumps(hello).encode() + b"\n"); stream.flush()
        ack = json.loads(stream.readline())
        self.assertEqual(ack["proof"], offer.mac_proof("phone-1", nonce))
        secret = self.vault.values["phone-1"]
        phone = SecureChannel("phone-1", secret)
        status = phone.seal("device.status", {"model":"Test Phone"})
        stream.write(json.dumps(status).encode() + b"\n"); stream.flush()
        for _ in range(50):
            if self.server.devices(): break
            threading.Event().wait(.01)
        self.assertEqual(self.server.devices()[0]["device_id"], "phone-1")
        self.server.alert("phone-1", True)
        command = json.loads(stream.readline())
        receiver = SecureChannel("phone-1", secret)
        self.assertEqual(receiver.open(command)[0], "alert.start")
        stream.write(json.dumps(status).encode() + b"\n"); stream.flush()
        for _ in range(50):
            if not self.server.devices(): break
            threading.Event().wait(.01)
        self.assertEqual(self.server.devices(), [])
        sock.close()


if __name__ == "__main__":
    unittest.main()

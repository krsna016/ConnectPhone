import unittest
import base64

from core.secure_companion import (
    CompanionSecurityError,
    PairingOffer,
    SecureChannel,
)


class MemoryVault:
    def __init__(self):
        self.values = {}

    def store(self, device_id, secret):
        self.values[device_id] = secret


class SecureCompanionTests(unittest.TestCase):
    PHONE_NONCE = base64.urlsafe_b64encode(b"n" * 24).rstrip(b"=").decode()

    def test_pairing_requires_phone_proof_and_derives_unique_key(self):
        vault = MemoryVault()
        offer = PairingOffer.create(now=100)
        proof = offer.phone_proof("phone-a", self.PHONE_NONCE)
        secret = offer.complete("phone-a", self.PHONE_NONCE, proof, vault, now=101)
        self.assertEqual(len(secret), 32)
        self.assertEqual(vault.values["phone-a"], secret)

        other = offer.complete(
            "phone-b", self.PHONE_NONCE, offer.phone_proof("phone-b", self.PHONE_NONCE), vault, now=101
        )
        self.assertNotEqual(secret, other)

    def test_expired_or_forged_pairing_is_rejected(self):
        offer = PairingOffer.create(now=100)
        with self.assertRaises(CompanionSecurityError):
            offer.complete("phone", self.PHONE_NONCE, "forged", MemoryVault(), now=101)
        with self.assertRaises(CompanionSecurityError):
            offer.complete(
                "phone", self.PHONE_NONCE, offer.phone_proof("phone", self.PHONE_NONCE), MemoryVault(), now=1000
            )
        with self.assertRaises(CompanionSecurityError):
            offer.complete("phone", "not-base64!", offer.phone_proof("phone", "not-base64!"), MemoryVault(), now=101)

    def test_authenticated_message_round_trip(self):
        clock = lambda: 1_000
        sender = SecureChannel("phone-a", b"s" * 32, clock=clock)
        receiver = SecureChannel("phone-a", b"s" * 32, clock=clock)
        message = sender.seal("alert.start", {"pattern": "siren"})
        self.assertEqual(receiver.open(message), ("alert.start", {"pattern": "siren"}))

    def test_tampering_replay_and_stale_messages_are_rejected(self):
        now = [1_000]
        sender = SecureChannel("phone-a", b"s" * 32, clock=lambda: now[0])
        receiver = SecureChannel("phone-a", b"s" * 32, clock=lambda: now[0])

        original = sender.seal("device.status", {})
        tampered = {**original, "ciphertext": original["ciphertext"][:-1] + ("A" if original["ciphertext"][-1] != "A" else "B")}
        with self.assertRaises(CompanionSecurityError):
            receiver.open(tampered)

        self.assertEqual(receiver.open(original), ("device.status", {}))
        with self.assertRaises(CompanionSecurityError):
            receiver.open(original)

        stale = sender.seal("device.status", {})
        now[0] += 60
        with self.assertRaises(CompanionSecurityError):
            receiver.open(stale)

    def test_lock_bypass_is_not_a_capability(self):
        channel = SecureChannel("phone-a", b"s" * 32)
        for capability in ("lockscreen.bypass", "clipboard.read", "file.pull", "accessibility.input"):
            with self.subTest(capability=capability), self.assertRaises(CompanionSecurityError):
                channel.seal(capability, {})


if __name__ == "__main__":
    unittest.main()

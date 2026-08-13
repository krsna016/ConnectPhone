import base64
import unittest

from core.qr_pairing import new_qr_credentials, svg_data_url


class QrPairingTests(unittest.TestCase):
    def test_aosp_payload_shape_and_fresh_credentials(self):
        service, password, payload = new_qr_credentials()
        self.assertRegex(service, r"^studio-[A-Za-z0-9]{10}$")
        self.assertRegex(password, r"^[A-Za-z0-9]{12}$")
        self.assertEqual(payload, f"WIFI:T:ADB;S:{service};P:{password};;")
        self.assertNotEqual(new_qr_credentials()[2], payload)

    def test_svg_is_local_data_url_without_plaintext_payload(self):
        payload = "WIFI:T:ADB;S:studio-AbCd123456;P:AbCd1234EfGh;;"
        url = svg_data_url(payload)
        self.assertTrue(url.startswith("data:image/svg+xml;base64,"))
        svg = base64.b64decode(url.split(",", 1)[1])
        self.assertIn(b"<svg", svg)
        self.assertNotIn(payload.encode(), svg)


if __name__ == "__main__":
    unittest.main()

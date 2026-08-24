import unittest
import http.client
import socketserver
import threading

import ConnectPhoneUI


class FakeHandler:
    def __init__(self, token):
        self.headers = {"X-ConnectPhone-Token": token}


class UiSecurityTests(unittest.TestCase):
    def test_api_token_requires_exact_header_value(self):
        self.assertTrue(ConnectPhoneUI._token_allowed(FakeHandler(ConnectPhoneUI.API_TOKEN)))
        for value in ("", ConnectPhoneUI.API_TOKEN + "x", " x" + ConnectPhoneUI.API_TOKEN):
            self.assertFalse(ConnectPhoneUI._token_allowed(FakeHandler(value)))

    def test_only_exact_loopback_dashboard_origins_are_allowed(self):
        for origin in (None, "null", "http://localhost:8282", "http://127.0.0.1:8282", "http://[::1]:8282"):
            self.assertTrue(ConnectPhoneUI._origin_allowed(origin))
        for origin in (
            "https://localhost:8282",
            "http://localhost:9999",
            "http://localhost.evil.example:8282",
            "http://192.168.1.2:8282",
            "not-a-url",
        ):
            self.assertFalse(ConnectPhoneUI._origin_allowed(origin))

    def test_endpoint_validation_rejects_non_ipv4_and_bad_ports(self):
        self.assertTrue(ConnectPhoneUI._valid_ipv4("192.0.2.1"))
        self.assertTrue(ConnectPhoneUI._valid_port(65535))
        for value in ("localhost", "::1", "192.0.2.999", {"ip": "192.0.2.1"}):
            self.assertFalse(ConnectPhoneUI._valid_ipv4(value))
        for value in (0, 65536, "bad", None):
            self.assertFalse(ConnectPhoneUI._valid_port(value))


class UiHttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            allow_reuse_address = True
            daemon_threads = True

        cls.server = Server(("127.0.0.1", 0), ConnectPhoneUI.ConnectPhoneUIHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, *, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        content = response.read()
        result = response.status, dict(response.getheaders()), content
        connection.close()
        return result

    def test_static_ui_has_security_headers_but_source_traversal_is_blocked(self):
        status, headers, _ = self.request("GET", "/index.html")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        status, _, _ = self.request("GET", "/../ConnectPhoneUI.py")
        self.assertEqual(status, 404)

    def test_api_rejects_missing_token_and_foreign_origin(self):
        status, _, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 401)
        status, _, _ = self.request("GET", "/api/status", headers={
            "X-ConnectPhone-Token": ConnectPhoneUI.API_TOKEN,
            "Origin": "https://evil.example",
        })
        self.assertEqual(status, 403)

    def test_unknown_routes_and_oversized_bodies_fail_closed(self):
        auth = {"X-ConnectPhone-Token": ConnectPhoneUI.API_TOKEN}
        status, _, _ = self.request("GET", "/api/not-real", headers=auth)
        self.assertEqual(status, 404)
        status, _, _ = self.request("POST", "/api/not-real", headers={
            **auth,
            "Content-Length": str(ConnectPhoneUI.MAX_REQUEST_BODY + 1),
        })
        self.assertEqual(status, 413)


if __name__ == "__main__":
    unittest.main()

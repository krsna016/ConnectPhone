"""Secure, bounded wrapper around ``adb pair``."""

import ipaddress
import re
import subprocess


_SUCCESS = re.compile(r"successfully\s+paired", re.IGNORECASE)


def pair_with_secret(endpoint, secret, runner=subprocess.run, timeout=15):
    """Pair once using a bounded printable secret supplied only over stdin."""
    if not isinstance(endpoint, str) or not re.fullmatch(r"[^\s\x00]+:\d{1,5}", endpoint):
        return False, "Invalid pairing endpoint"
    host, raw_port = endpoint.rsplit(":", 1)
    try:
        if not isinstance(ipaddress.ip_address(host), ipaddress.IPv4Address) or not 1 <= int(raw_port) <= 65535:
            return False, "Invalid pairing endpoint"
    except ValueError:
        return False, "Invalid pairing endpoint"
    if not isinstance(secret, str) or not re.fullmatch(r"[\x21-\x7e]{6,64}", secret):
        return False, "Invalid pairing secret"
    try:
        result = runner(
            ["adb", "pair", endpoint],
            input=f"{secret}\n",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "Pairing timed out; request a fresh code and try again"
    except OSError as exc:
        return False, f"Could not start adb: {exc}"
    output = " ".join(part.strip() for part in (result.stdout or "", result.stderr or "") if part.strip())
    # Never echo the one-time code back into API responses or logs.
    output = output.replace(secret, "[REDACTED]")
    if result.returncode == 0 and _SUCCESS.search(output):
        return True, output
    return False, output or f"adb pair exited with status {result.returncode}"


def pair_with_code(endpoint, code, runner=subprocess.run, timeout=15):
    """Pair with Android's six-digit manual code."""
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        return False, "Pairing code must contain exactly six digits"
    return pair_with_secret(endpoint, code, runner=runner, timeout=timeout)

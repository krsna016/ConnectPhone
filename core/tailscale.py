"""Tailscale mesh network detection and peer discovery for ConnectPhone."""

import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Dict, List, Optional

try:
    import ifaddr
except ImportError:
    ifaddr = None

logger = logging.getLogger(__name__)

TAILSCALE_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
_TS_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9.\-_]+\.(?:ts\.net|tailscale\.net)$", re.IGNORECASE)
_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$")


def is_tailscale_ip(ip_or_host: str) -> bool:
    """Return True if the target is a Tailscale CGNAT IP (100.64.0.0/10) or MagicDNS domain."""
    if not isinstance(ip_or_host, str):
        return False
    value = ip_or_host.strip()
    if not value:
        return False
    if _TS_DOMAIN_RE.match(value):
        return True
    try:
        addr = ipaddress.ip_address(value)
        return isinstance(addr, ipaddress.IPv4Address) and addr in TAILSCALE_CGNAT_NET
    except ValueError:
        return False


def is_valid_host_or_ip(value: str) -> bool:
    """Return True if value is a valid IPv4 address or valid hostname (including MagicDNS)."""
    if not isinstance(value, str):
        return False
    target = value.strip()
    if not target or len(target) > 253:
        return False
    try:
        addr = ipaddress.ip_address(target)
        return isinstance(addr, ipaddress.IPv4Address) and not addr.is_unspecified
    except ValueError:
        pass
    # Validate hostname / MagicDNS (requires valid domain structure e.g. phone.ts.net)
    if _TS_DOMAIN_RE.match(target):
        return True
    if "." in target and bool(_HOSTNAME_RE.match(target)):
        parts = target.split(".")
        if len(parts[-1]) >= 2 and not parts[-1].isdigit():
            return True
    return False



def get_local_tailscale_ip() -> Optional[str]:
    """Inspect network interfaces to find the Mac's assigned Tailscale IPv4 address."""
    if ifaddr is not None:
        try:
            for adapter in ifaddr.get_adapters():
                for ip in adapter.ips:
                    if isinstance(ip.ip, str):
                        try:
                            addr = ipaddress.ip_address(ip.ip)
                            if isinstance(addr, ipaddress.IPv4Address) and addr in TAILSCALE_CGNAT_NET:
                                return str(addr)
                        except ValueError:
                            continue
        except Exception as exc:
            logger.debug("ifaddr inspection failed: %s", exc)

    # Fallback to ifconfig parsing
    try:
        res = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=1.5)
        for match in re.finditer(r"inet\s+(100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+)", res.stdout):
            return match.group(1)
    except Exception:
        pass

    return None


def is_tailscale_installed() -> bool:
    """Check if Tailscale macOS application or CLI is installed on this Mac."""
    common_locations = [
        "/Applications/Tailscale.app",
        os.path.expanduser("~/Applications/Tailscale.app"),
        "/usr/local/bin/tailscale",
        "/opt/homebrew/bin/tailscale",
    ]
    for path in common_locations:
        if os.path.exists(path):
            return True
    return bool(shutil.which("tailscale"))


def get_tailscale_peers() -> List[Dict[str, any]]:
    """Query local Tailscale CLI for online peers (specifically Android phones)."""
    ts_cli = shutil.which("tailscale")
    if not ts_cli:
        for candidate in ["/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale"]:
            if os.path.exists(candidate) and os.access(candidate, os.X_OK):
                ts_cli = candidate
                break

    if not ts_cli:
        return []

    try:
        proc = subprocess.run(
            [ts_cli, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []

        data = json.loads(proc.stdout)
        peer_status = data.get("PeerStatus", {})
        peers = []
        for _, peer_info in peer_status.items():
            if not isinstance(peer_info, dict):
                continue
            peer_ips = peer_info.get("TailscaleIPs", [])
            ipv4_list = [ip for ip in peer_ips if is_tailscale_ip(ip)]
            if not ipv4_list:
                continue
            host_name = peer_info.get("HostName") or peer_info.get("DNSName", "").rstrip(".")
            os_name = (peer_info.get("OS") or "").lower()
            is_online = bool(peer_info.get("Online", False))
            peers.append({
                "name": host_name,
                "ip": ipv4_list[0],
                "all_ips": ipv4_list,
                "os": os_name,
                "online": is_online,
                "is_android": os_name == "android",
            })
        return peers
    except Exception as exc:
        logger.debug("Could not query Tailscale peers: %s", exc)
        return []


_STATUS_CACHE: Optional[Dict[str, any]] = None
_STATUS_CACHE_TIME: float = 0.0
_STATUS_CACHE_TTL: float = 4.0


def get_tailscale_status(force_refresh: bool = False) -> Dict[str, any]:
    """Return consolidated Tailscale status for UI and backend supervisors (cached for 4s)."""
    global _STATUS_CACHE, _STATUS_CACHE_TIME
    now = time.monotonic()
    if not force_refresh and _STATUS_CACHE is not None and (now - _STATUS_CACHE_TIME) < _STATUS_CACHE_TTL:
        return _STATUS_CACHE

    installed = is_tailscale_installed()
    local_ip = get_local_tailscale_ip()
    running = bool(local_ip)

    peers = get_tailscale_peers() if running else []
    android_peers = [p for p in peers if p.get("is_android")]

    result = {
        "installed": installed,
        "running": running,
        "mac_ip": local_ip,
        "peers": peers,
        "android_peers": android_peers,
    }
    _STATUS_CACHE = result
    _STATUS_CACHE_TIME = now
    return result


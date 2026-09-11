"""Persistent, identity-pinned Wireless ADB connection supervisor."""

import ipaddress
import asyncio
import logging
import os
import subprocess
import threading
import time

from core.tailscale import is_tailscale_ip
from core.config_manager import persist_current_endpoint
from core.adb_lifecycle import endpoint_port_open, load_saved_devices, read_transport_identity, reset_wireless_transport, wireless_transport_states
from core.mdns_scanner import ZeroPingScanner
from core.paths import migrate_legacy_config


def discover_open_tcp_ports(ip, stop_event=None, start_port=30000, end_port=65535, timeout=0.12):
    """Find candidate Android dynamic ports on one trusted LAN address.

    The scan only identifies open TCP listeners. The caller must still use
    ADB authentication and verify the pinned Android hardware identity before
    accepting or persisting any endpoint.
    """
    async def probe(port):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return port
        except (OSError, asyncio.TimeoutError):
            return None

    async def scan():
        found = []
        batch_size = 512
        for first in range(int(start_port), int(end_port) + 1, batch_size):
            if stop_event is not None and stop_event.is_set():
                break
            last = min(first + batch_size, int(end_port) + 1)
            results = await asyncio.gather(*(probe(port) for port in range(first, last)))
            found.extend(port for port in results if port is not None)
        return sorted(set(found))

    try:
        return asyncio.run(scan())
    except (OSError, RuntimeError, ValueError):
        return []


class AutoReconnector:
    # ADB's host daemon occasionally stalls while scrcpy, file transfers, or
    # dashboard probes are opening transports.  Do not turn one such stall
    # into a user-visible disconnect.
    OFFLINE_GRACE = 4.0
    EXPLICIT_OFFLINE_GRACE = 1.0
    KEEPALIVE_INTERVAL = 4.0
    KEEPALIVE_FAILURE_LIMIT = 3
    LOOP_INTERVAL = 1.0
    CONNECT_TIMEOUT = 3.5
    PORT_SCAN_COOLDOWN = 20.0

    def __init__(self, config_path=None, scanner=None, command_runner=None, busy_check=None, port_discoverer=None, on_change=None):
        self.logger = logging.getLogger(__name__)
        self.config_path = config_path or migrate_legacy_config()
        self.scanner = scanner or ZeroPingScanner()
        self._run = command_runner or subprocess.run
        self._busy_check = busy_check or (lambda: False)
        self._discover_ports = port_discoverer or discover_open_tcp_ports
        self._on_change = on_change
        self._thread = None
        self._stop_event = threading.Event()
        self.connected_endpoints = set()
        self._endpoint_serial = {}
        self._pending_identity = {}
        self._offline_since = {}
        self._next_attempt = {}
        self._failures = {}
        self._last_reset = {}
        self._last_keepalive = {}
        self._keepalive_failures = {}
        self._last_port_scan = {}
        self._manually_disconnected = set()
        self._manually_disconnected_all = False
        self._seen_mdns_ports = {}
        self._last_ts_wake = {}

    def pause_auto_reconnect(self, target=None):
        """Pause auto-reconnecting after an explicit user disconnect."""
        if target:
            self._manually_disconnected.add(str(target))
            self.connected_endpoints.discard(str(target))
        else:
            self._manually_disconnected_all = True
            self.connected_endpoints.clear()
        self._offline_since.clear()

    def unpause_auto_reconnect(self, target=None):
        """Resume auto-reconnecting when user requests connection."""
        if target:
            target_str = str(target)
            self._manually_disconnected.discard(target_str)
            for k in list(self._failures):
                if target_str in k:
                    self._failures.pop(k, None)
            for k in list(self._next_attempt):
                if target_str in k:
                    self._next_attempt.pop(k, None)
            for k in list(self._last_ts_wake):
                if target_str in k:
                    self._last_ts_wake.pop(k, None)
        else:
            self._manually_disconnected.clear()
            self._manually_disconnected_all = False
            self._failures.clear()
            self._next_attempt.clear()
            self._last_ts_wake.clear()

    def _notify_change(self):
        if callable(self._on_change):
            try:
                self._on_change()
            except Exception:
                pass

    def start_watching(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        if hasattr(self.scanner, "start"):
            try:
                self.scanner.start()
            except Exception:
                pass
        self._thread = threading.Thread(target=self._watch_loop, name="ConnectPhone-Reconnect", daemon=True)
        self._thread.start()

    def _watch_loop(self):
        while not self._stop_event.is_set():
            try:
                trusted = self._trusted_devices()
                trusted_serials = {item["serial"] for item in trusted}
                states = self._verify_connections(trusted_serials)
                # An unavailable ADB daemon is not evidence that every phone
                # disconnected. Preserve state and try again next iteration.
                if states is None:
                    self._stop_event.wait(self.LOOP_INTERVAL)
                    continue
                self._keepalive(states)
                discovered = self.scanner.find_devices_instantly(search_time=0.5)

                # If phone wireless debugging was toggled on the phone (fresh/changed port),
                # automatically lift manual disconnect pause!
                for device in discovered:
                    if device.get("type", "connect") == "connect" and "ip" in device and "port" in device:
                        ip, port = str(device["ip"]), int(device["port"])
                        prev_port = self._seen_mdns_ports.get(ip)
                        if prev_port is not None and prev_port != port:
                            # Port changed -> user toggled Wireless Debugging!
                            self.unpause_auto_reconnect(ip)
                            self._manually_disconnected_all = False
                        self._seen_mdns_ports[ip] = port

                # Query online Tailscale Android peers for cellular/remote failover
                ts_peer_endpoints = []
                try:
                    from core.tailscale import get_tailscale_peers
                    ts_peers = get_tailscale_peers()
                    for peer in ts_peers:
                        if peer.get("online") and peer.get("ip"):
                            ts_peer_endpoints.append(f"{peer['ip']}:5555")
                except Exception:
                    pass

                for item in trusted:
                    serial = item["serial"]
                    ip = item["ip"]
                    if self._manually_disconnected_all:
                        continue
                    if serial in self._manually_disconnected or ip in self._manually_disconnected:
                        continue
                    if any(states.get(ep) == "device" and value == serial for ep, value in self._endpoint_serial.items()):
                        self._maybe_promote_to_local_wifi(item, discovered, states)
                        continue
                    matches = []
                    for device in discovered:
                        if device.get("type", "connect") != "connect":
                            continue
                        hint = device.get("device_serial_hint")
                        if hint == serial or (not hint and device.get("ip") == item["ip"]):
                            matches.append(f"{device['ip']}:{int(device['port'])}")

                    primary_ep = f"{item['ip']}:{item['port']}"
                    fallback_eps = item.get("fallback_endpoints", [])
                    primary_failing = self._failures.get(primary_ep, 0) > 0 or primary_ep in self._offline_since

                    expanded_ts = list(ts_peer_endpoints)
                    if item.get("port") and int(item["port"]) != 5555:
                        for ts_ep in ts_peer_endpoints:
                            ts_host = ts_ep.split(":", 1)[0]
                            expanded_ts.append(f"{ts_host}:{int(item['port'])}")

                    candidates = list(matches)
                    if not primary_failing:
                        candidates.append(primary_ep)
                        candidates.extend(fallback_eps)
                        candidates.extend(expanded_ts)
                    else:
                        candidates.extend(fallback_eps)
                        candidates.extend(expanded_ts)
                        candidates.append(primary_ep)

                    if not candidates:
                        candidates = [primary_ep]

                    for endpoint in dict.fromkeys(candidates):
                        if endpoint in self.connected_endpoints:
                            continue
                        is_ts = is_tailscale_ip(endpoint.split(":")[0])
                        if states.get(endpoint) == "device":
                            ident = read_transport_identity(self._run, endpoint, timeout=6 if is_ts else 2.5)
                            if ident == serial:
                                self._mark_connected(endpoint, ident)
                                break
                            elif ident is not None and ident != serial:
                                reset_wireless_transport(self._run, endpoint, restart_daemon=False)
                                states.pop(endpoint, None)
                            else:
                                self._schedule_failure(endpoint)
                        if self._try_connect(endpoint, serial):
                            break
                    else:
                        self._recover_rotated_port(item)
            except Exception:
                self.logger.exception("Wireless supervisor iteration failed")
            self._stop_event.wait(self.LOOP_INTERVAL)

    def _trusted_devices(self):
        devices = []
        seen = set()
        for item in load_saved_devices(self.config_path):
            if not isinstance(item, dict) or not item.get("auto_reconnect", True):
                continue
            ip, port, serial = item.get("ip"), item.get("port"), str(item.get("device_serial") or "").strip()
            try:
                valid = (isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address) or is_tailscale_ip(ip)) and 1 <= int(port) <= 65535
            except (ValueError, TypeError):
                valid = is_tailscale_ip(ip) and 1 <= int(port) <= 65535

            if valid and serial and serial not in seen:
                seen.add(serial)
                fallbacks = [
                    str(ep).strip() for ep in item.get("fallback_endpoints", [])
                    if isinstance(ep, str) and ":" in ep
                ]
                devices.append({"ip": ip, "port": int(port), "serial": serial, "fallback_endpoints": fallbacks})
        return devices

    def _run_adb(self, args, timeout):
        return self._run(["adb", *args], capture_output=True, text=True, timeout=timeout)

    def _schedule_failure(self, endpoint):
        failures = min(self._failures.get(endpoint, 0) + 1, 8)
        self._failures[endpoint] = failures
        self._next_attempt[endpoint] = time.monotonic() + min(4.0, 0.5 * (2 ** (failures - 1)))

    def _record_healthy(self, endpoint):
        """Forget old failures after an authoritative command succeeds.

        The previous implementation accumulated isolated failures for the
        lifetime of the process. Four unrelated timeouts hours apart could
        therefore trigger ``adb disconnect`` against a currently healthy
        phone. Recovery decisions must be based on consecutive failures.
        """
        self._failures.pop(endpoint, None)
        self._next_attempt.pop(endpoint, None)
        self._offline_since.pop(endpoint, None)
        self._keepalive_failures.pop(endpoint, None)

    def _mark_connected(self, endpoint, serial):
        self.connected_endpoints.add(endpoint)
        self._endpoint_serial[endpoint] = serial
        self._pending_identity.pop(endpoint, None)
        self._record_healthy(endpoint)
        # Update active serial if no target was selected or if current target belonged to this phone
        current_target = os.environ.get("ANDROID_SERIAL", "")
        if not current_target or current_target not in self.connected_endpoints or self._endpoint_serial.get(current_target) == serial:
            os.environ["ANDROID_SERIAL"] = endpoint
        # Disconnect and clean up any stale ghost endpoints that belonged to this same serial
        for old_ep, old_serial in list(self._endpoint_serial.items()):
            if old_serial == serial and old_ep != endpoint:
                self.connected_endpoints.discard(old_ep)
                self._endpoint_serial.pop(old_ep, None)
                self._pending_identity.pop(old_ep, None)
                self._offline_since.pop(old_ep, None)
                self._failures.pop(old_ep, None)
                self._keepalive_failures.pop(old_ep, None)
                self._last_keepalive.pop(old_ep, None)
                reset_wireless_transport(self._run, old_ep, restart_daemon=False)
        if not persist_current_endpoint(self.config_path, endpoint, serial):
            self.logger.warning("Could not persist wireless endpoint %s", endpoint)
        self._notify_change()

    @staticmethod
    def _port_open(endpoint):
        host = endpoint.split(":", 1)[0] if ":" in endpoint else endpoint
        if is_tailscale_ip(host):
            try:
                from core.tailscale import wake_tailscale_peer
                wake_tailscale_peer(host, timeout=1.0)
            except Exception:
                pass
            timeout = 5.0
        else:
            timeout = 4.0
        return endpoint_port_open(endpoint, timeout=timeout)

    def _maybe_reset_stale_endpoint(self, endpoint):
        now = time.monotonic()
        failures = self._failures.get(endpoint, 0)
        # If the phone's TCP endpoint is reachable but ADB is still stuck, the
        # daemon has retained a stale transport. Recover it promptly instead of
        # leaving the UI offline for minutes. Never restart a shared daemon
        # while another wireless target is attached.
        hard = failures >= 5
        reset_key = f"{endpoint}#daemon" if hard else endpoint
        cooldown = 600 if hard else 60
        last_reset = self._last_reset.get(reset_key)
        if self._busy_check() or failures < 4 or (last_reset is not None and now - last_reset < cooldown):
            return False
        port_up = self._port_open(endpoint)
        if hard and not port_up:
            hard = False
        if hard:
            states = wireless_transport_states(self._run)
            if any(candidate != endpoint for candidate in states):
                hard = False
                reset_key = endpoint
                cooldown = 60
                last_reset = self._last_reset.get(reset_key)
                if last_reset is not None and now - last_reset < cooldown:
                    return False
        if reset_wireless_transport(self._run, endpoint, restart_daemon=hard):
            self.logger.warning("Recovered stale wireless transport: %s", endpoint)
            self._last_reset[reset_key] = now
            self._next_attempt[endpoint] = now + 2.0
            return True
        return False

    def _try_connect(self, endpoint, expected_serial):
        if not expected_serial or time.monotonic() < self._next_attempt.get(endpoint, 0):
            return False
        host = endpoint.split(":", 1)[0] if ":" in endpoint else endpoint
        is_ts = is_tailscale_ip(host)
        now = time.monotonic()
        if is_ts and now - self._last_ts_wake.get(host, 0) > 12.0:
            self._last_ts_wake[host] = now
            try:
                from core.tailscale import wake_tailscale_peer
                wake_tailscale_peer(host, timeout=2.0)
            except Exception:
                pass
        connect_timeout = 8.0 if is_ts else self.CONNECT_TIMEOUT
        ident_timeout = 8.0 if is_ts else 6.0
        try:
            result = self._run_adb(["connect", endpoint], connect_timeout)
            output = f"{result.stdout or ''} {result.stderr or ''}".lower()
            if "connected to" not in output and "already connected" not in output:
                self._schedule_failure(endpoint)
                self._maybe_reset_stale_endpoint(endpoint)
                return False
            identity = read_transport_identity(self._run, endpoint, timeout=ident_timeout)
            if identity is None:
                if "already connected" in output:
                    self.logger.warning("Clearing dead 'already connected' transport: %s", endpoint)
                    reset_wireless_transport(self._run, endpoint, restart_daemon=False)
                self._pending_identity[endpoint] = expected_serial
                self._schedule_failure(endpoint)
                self._maybe_reset_stale_endpoint(endpoint)
                return False
            if identity != expected_serial:
                self.logger.error("Rejected wireless identity at %s", endpoint)
                self._run_adb(["disconnect", endpoint], 6)
                self._schedule_failure(endpoint)
                return False
            self._mark_connected(endpoint, identity)
            return True
        except (OSError, subprocess.TimeoutExpired):
            self._schedule_failure(endpoint)
            return False

    def _recover_rotated_port(self, item):
        """Recover an already-paired phone when Android rotates its TLS port."""
        if self._busy_check():
            return False
        ip = str(item.get("ip") or "").strip()
        expected_serial = str(item.get("serial") or "").strip()
        old_port = int(item.get("port") or 0)
        if not ip or not expected_serial:
            return False
        now = time.monotonic()
        if now - self._last_port_scan.get(expected_serial, 0) < self.PORT_SCAN_COOLDOWN:
            return False
        self._last_port_scan[expected_serial] = now

        priority_ports = []
        if old_port != 5555:
            priority_ports.append(5555)
        for p in priority_ports:
            ep = f"{ip}:{p}"
            if self._try_connect(ep, expected_serial):
                self.logger.info("Recovered standard Wireless Debugging port: %s", ep)
                return True

        try:
            ports = self._discover_ports(ip, self._stop_event)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        for port in ports:
            try:
                port = int(port)
            except (TypeError, ValueError):
                continue
            if not 1 <= port <= 65535 or port == old_port:
                continue
            endpoint = f"{ip}:{port}"
            if self._try_connect(endpoint, expected_serial):
                self.logger.info("Recovered rotated Wireless Debugging port: %s", endpoint)
                return True
        return False

    def _maybe_promote_to_local_wifi(self, item, discovered, states):
        """Seamlessly promote a phone currently connected on Tailscale/WAN to local Wi-Fi."""
        if self._busy_check():
            return False
        serial = item["serial"]
        active_ep = next((ep for ep, s in self._endpoint_serial.items() if s == serial and states.get(ep) == "device"), None)
        if not active_ep:
            return False
        host = active_ep.split(":", 1)[0] if ":" in active_ep else active_ep
        if not is_tailscale_ip(host):
            return False

        now = time.monotonic()
        last_promo = getattr(self, "_last_promo_check", {})
        if now - last_promo.get(serial, 0) < 5.0:
            return False
        last_promo[serial] = now
        self._last_promo_check = last_promo

        local_candidates = []
        for device in discovered:
            if device.get("type", "connect") == "connect":
                dip = str(device.get("ip", "")).strip()
                hint = device.get("device_serial_hint")
                if dip and not is_tailscale_ip(dip) and (hint == serial or not hint):
                    local_candidates.append(f"{dip}:{int(device['port'])}")

        for ep in item.get("fallback_endpoints", []):
            e_host = ep.split(":", 1)[0] if ":" in ep else ep
            if e_host and not is_tailscale_ip(e_host) and ep not in local_candidates:
                local_candidates.append(ep)

        p_ip = str(item.get("ip", "")).strip()
        if p_ip and not is_tailscale_ip(p_ip):
            p_ep = f"{p_ip}:{item.get('port', 5555)}"
            if p_ep not in local_candidates:
                local_candidates.append(p_ep)

        for cand in dict.fromkeys(local_candidates):
            if cand == active_ep:
                continue
            if self._port_open(cand):
                self.logger.info("Local Wi-Fi candidate %s is open for %s; promoting to gigabit connection", cand, serial)
                if self._try_connect(cand, serial):
                    self.logger.info("Promoted %s from Tailscale (%s) to local Wi-Fi (%s)", serial, active_ep, cand)
                    return True
        return False

    def _verify_connections(self, trusted_serials):
        states = wireless_transport_states(self._run, unavailable=None)
        if states is None:
            self.logger.warning("ADB device-state query unavailable; retaining wireless connections")
            return None
        now = time.monotonic()
        for endpoint in list(self.connected_endpoints):
            state = states.get(endpoint)
            if state == "device":
                self._offline_since.pop(endpoint, None)
                if endpoint in self._pending_identity and now >= self._next_attempt.get(endpoint, 0):
                    is_ts = is_tailscale_ip(endpoint.split(":")[0])
                    identity = read_transport_identity(self._run, endpoint, timeout=6 if is_ts else 3)
                    expected = self._pending_identity[endpoint]
                    if identity == expected:
                        self._mark_connected(endpoint, identity)
                    elif identity:
                        self._run_adb(["disconnect", endpoint], 6)
                        self.connected_endpoints.discard(endpoint)
                        self._pending_identity.pop(endpoint, None)
                        self._offline_since.pop(endpoint, None)
                        self._schedule_failure(endpoint)
                    else:
                        self._schedule_failure(endpoint)
                continue
            since = self._offline_since.setdefault(endpoint, now)
            is_ts = is_tailscale_ip(endpoint.split(":")[0])
            explicit_grace = 10.0 if is_ts else 6.0
            grace = explicit_grace if state == "offline" else (12.0 if is_ts else 8.0)
            if now - since < grace:
                continue
            self.connected_endpoints.discard(endpoint)
            self._endpoint_serial.pop(endpoint, None)
            self._pending_identity.pop(endpoint, None)
            self._offline_since.pop(endpoint, None)
            self._last_keepalive.pop(endpoint, None)
            self._keepalive_failures.pop(endpoint, None)
            if (state in {"offline", "authorizing", "unauthorized"} or state is None) and reset_wireless_transport(self._run, endpoint, restart_daemon=False):
                # `adb devices` explicitly confirmed a dead/hung TLS
                # transport. Clearing it is required before `adb connect` can
                # create a fresh transport to the same advertised endpoint.
                self.logger.warning("Cleared confirmed %s wireless transport: %s", state or "stale", endpoint)
                self._last_reset[endpoint] = now
                self._next_attempt[endpoint] = now + 0.5
            else:
                self._schedule_failure(endpoint)
                self._maybe_reset_stale_endpoint(endpoint)
        for endpoint, state in states.items():
            if endpoint in self.connected_endpoints:
                continue
            if state in {"offline", "authorizing", "unauthorized"}:
                failures = self._failures.get(endpoint, 0) + 1
                self._failures[endpoint] = failures
                if failures >= 2 and reset_wireless_transport(self._run, endpoint, restart_daemon=False):
                    self.logger.warning("Cleared stuck %s wireless transport: %s", state, endpoint)
                    self._last_reset[endpoint] = now
                    self._next_attempt[endpoint] = now + 0.5
                    self._failures.pop(endpoint, None)
                continue
            if state != "device" or now < self._next_attempt.get(endpoint, 0):
                continue
            is_ts = is_tailscale_ip(endpoint.split(":")[0])
            identity = read_transport_identity(self._run, endpoint, timeout=6 if is_ts else 3)
            if identity in trusted_serials:
                self._mark_connected(endpoint, identity)
            elif identity is None:
                # `adb devices` can retain a dead TLS transport as "device".
                # A real shell identity probe is authoritative; keep advancing
                # recovery instead of accepting the stale list entry forever.
                self._schedule_failure(endpoint)
                failures = self._failures.get(endpoint, 0)
                if failures >= 2 and reset_wireless_transport(self._run, endpoint, restart_daemon=False):
                    self.logger.warning("Cleared dead ghost transport: %s", endpoint)
                    self._last_reset[endpoint] = now
                    self._next_attempt[endpoint] = now + 0.5
                else:
                    self._maybe_reset_stale_endpoint(endpoint)
        return states

    def _keepalive(self, states):
        if self._busy_check():
            return
        now = time.monotonic()
        for endpoint in list(self.connected_endpoints):
            if states.get(endpoint) != "device" or now - self._last_keepalive.get(endpoint, 0) < self.KEEPALIVE_INTERVAL:
                continue

            is_ts = is_tailscale_ip(endpoint.split(":")[0])
            probe_timeout = 5.0 if is_ts else 2.0
            failure_limit = 4 if is_ts else self.KEEPALIVE_FAILURE_LIMIT

            port_up = self._port_open(endpoint)
            if port_up:
                try:
                    result = self._run_adb(["-s", endpoint, "shell", "true"], probe_timeout)
                    if result.returncode == 0:
                        self._last_keepalive[endpoint] = now
                        self._record_healthy(endpoint)
                        continue
                except (OSError, subprocess.TimeoutExpired):
                    pass

            failures = self._keepalive_failures.get(endpoint, 0) + 1
            self._keepalive_failures[endpoint] = failures
            limit = (3 if not port_up else failure_limit) if is_ts else (2 if not port_up else failure_limit)
            if failures < limit:
                if is_ts:
                    try:
                        from core.tailscale import wake_tailscale_peer
                        wake_tailscale_peer(endpoint.split(":")[0], timeout=1.5)
                    except Exception:
                        pass
                self.logger.warning(
                    "Wireless health probe missed for %s (%d/%d, port_up=%s); connection retained",
                    endpoint,
                    failures,
                    limit,
                    port_up,
                )
                continue

            # Authoritative failure: network port unreachable or consecutive command timeouts.
            self.logger.warning("Wireless connection dropped on %s (port_up=%s); entering fast failover", endpoint, port_up)
            self.connected_endpoints.discard(endpoint)
            self._endpoint_serial.pop(endpoint, None)
            self._pending_identity.pop(endpoint, None)
            self._offline_since.pop(endpoint, None)
            self._last_keepalive.pop(endpoint, None)
            self._keepalive_failures.pop(endpoint, None)
            self._schedule_failure(endpoint)
            reset_wireless_transport(self._run, endpoint, restart_daemon=False)
            states.pop(endpoint, None)
            self._notify_change()

    def stop_watching(self):
        self._stop_event.set()
        self.scanner.stop()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=4)
        self._thread = None

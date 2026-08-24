# Secure Companion Mode

Secure Companion Mode is the non-ADB connection path for phones whose Developer
options are disabled. It is designed for devices the operator owns or is
authorized to manage. It is not a lock-screen or Android-permission bypass.

## Security boundary

- Explicit, short-lived QR enrollment; no silent discovery enrollment.
- A unique 256-bit secret for every phone, derived only after mutual proof.
- Long-term secrets stored in macOS Keychain, never in `config.json`.
- Every command payload is encrypted with AES-256-GCM and is also authenticated,
  device-bound, timestamped and sequenced.
- Replayed, stale, reordered, oversized and unauthorized messages are rejected.
- Capability allowlisting prevents the transport from gaining new powers merely
  because a peer sends a new command name.
- Revocation deletes one phone's key without affecting the rest of the fleet.
- Android consent remains authoritative for screen capture and Accessibility.

## Intended transport

The Android companion makes an outbound local-network connection to the Mac
address enrolled by QR. Sensitive payloads use AES-256-GCM with authenticated
headers, while an independent HMAC, sequence numbers and per-device keys reject
tampering, stale packets, replay and cross-device routing. The raw socket is not
treated as trusted, even on a private Wi-Fi network.

The companion uses Android Keystore for its long-term key, pins the per-device
key derived during pairing, displays a foreground-service notification, and
provides a user-accessible disconnect/revoke control. The current companion
implements encrypted device status and start/stop alert commands. It does not
request MediaProjection or Accessibility privileges.

## Explicitly out of scope

- Enabling ADB or Developer options remotely.
- Bypassing PIN, password, biometrics, app locks or factory-reset protection.
- Silently granting screen-capture, Accessibility or other restricted access.
- Root, exploit, custom-ROM or OEM-signing instructions.

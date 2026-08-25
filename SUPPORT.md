# Support

ConnectPhone is a macOS application for authorized Android devices. When
asking for help, include the macOS version, ConnectPhone commit or release,
Android version, connection type (USB or wireless), and the exact error.

Do not include ADB pairing details, QR payloads, API tokens, Android PINs,
Keychain contents, signing keys, or private device data in an issue or log.

## Common checks

- Confirm Android USB or Wireless Debugging is enabled and the device is
  authorized in ADB.
- Confirm `adb`, `scrcpy`, and `ffmpeg` are installed and available on `PATH`.
- Grant Accessibility and Screen Recording permissions to the app or Terminal
  when running from source.
- For wireless connections, confirm the Mac and phone can reach each other and
  that the phone's current pairing address and port are being used.
- For source builds, use Python 3.13 and follow the commands in the README.

If the problem involves a suspected security weakness, use the private process
in [SECURITY.md](SECURITY.md) instead of opening an issue.

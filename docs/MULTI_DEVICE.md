# Multi-device setup and control

ConnectPhone can enroll up to 10 trusted Android phones. Trust is pinned to
each phone's physical Android serial, so a changing Wireless Debugging port
does not make the phone a different device.

## Add a new phone

1. Put the Mac and phone on the same Wi-Fi network.
2. On Android, enable Developer options, USB debugging, and Wireless
   debugging.
3. In Wireless debugging, choose **Pair device with pairing code**.
4. In ConnectPhone's **Connection Center**, enter the displayed IP, pairing
   port, and current six-digit code. Keep the Android pairing dialog open until
   the operation completes.
5. Connect using the separate connection port shown on Android's main Wireless
   debugging screen. After its identity is verified, the phone appears in
   **Device Fleet** and automatic reconnect is enabled.

Pairing is required once per new phone. On later launches, **Reconnect All**
tries every trusted phone; there is no single auto-connect winner.

## When several phones are online

- Every online phone remains connected to ADB.
- One phone is marked **Selected**. Storage, Metrics, and older single-phone
  controls operate on that selected phone.
- Selecting another phone never disconnects the others.
- **Screen**, **Camera**, **Audio**, and **Call** create independently tracked scrcpy
  sessions with an explicit device serial and a separately titled window.
- **Mirror All Screens** opens one controllable screen window per online phone.
- **Wake All**, **Home All**, and **Sleep All** send explicitly routed commands
  to every online phone.

Screen and camera sessions may run together when the Android model supports
concurrent capture. Fleet camera previews default to video-only so they do not
compete with screen-mirror audio capture.

## Call audio

**Call** requests the phone's combined telephony uplink and downlink, so both
sides of an active cellular call can play on the Mac. It does not place, answer,
or route the Mac microphone into a call. Android reserves this capture source
for privileged system components, so availability depends on the phone vendor
and ROM. ConnectPhone requires the audio source to open and reports the real
scrcpy/Android error if the phone refuses it.

## Practical capacity

The registry limit is 10 phones and the process safety limit is 20 simultaneous
scrcpy sessions. Actual smooth-stream capacity depends on Mac CPU/GPU, Wi-Fi
bandwidth, phone encoders, and chosen modes. Fleet screen mirrors use a bounded
8 Mbps wireless profile, but ten simultaneous screens can still require around
80 Mbps before protocol overhead. For critical control, use strong Wi-Fi 6/6E
or USB for some phones.

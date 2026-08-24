package com.krsna016.connectphone.companion;

import android.Manifest;
import android.app.NotificationManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.ComponentActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.google.zxing.client.android.Intents;
import com.journeyapps.barcodescanner.ScanContract;
import com.journeyapps.barcodescanner.ScanOptions;
import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.Socket;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.concurrent.Executors;

public class MainActivity extends ComponentActivity {
    private TextView status;
    private SecureStore store;
    private final ActivityResultLauncher<ScanOptions> scanner = registerForActivityResult(new ScanContract(), result -> {
        if (result.getContents() != null) pair(result.getContents());
    });

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        store = new SecureStore(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL); root.setGravity(Gravity.CENTER_HORIZONTAL);
        root.setPadding(48, 80, 48, 48); root.setBackgroundColor(Color.rgb(10, 14, 24));
        TextView title = text(getString(R.string.app_name), 26, Color.WHITE); root.addView(title);
        TextView description = text(getString(R.string.description), 16, Color.LTGRAY);
        description.setPadding(0, 24, 0, 36); root.addView(description);
        status = text(getString(store.paired() ? R.string.status_paired : R.string.status_not_paired), 17,
            store.paired() ? Color.rgb(80, 220, 150) : Color.rgb(255, 190, 80)); root.addView(status);
        Button scan = new Button(this); scan.setText(R.string.scan_pairing_qr); scan.setOnClickListener(v -> scan()); root.addView(scan);
        Button connect = new Button(this); connect.setText(R.string.reconnect_now); connect.setOnClickListener(v -> startBridge()); root.addView(connect);
        Button revoke = new Button(this); revoke.setText(R.string.forget_mac); revoke.setOnClickListener(v -> {
            stopService(new Intent(this, CompanionService.class)); store.clear(); status.setText(R.string.pairing_removed);
        }); root.addView(revoke);
        TextView safety = text(getString(R.string.safety_notice), 14, Color.GRAY);
        safety.setPadding(0, 40, 0, 0); root.addView(safety);
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(this, new String[]{Manifest.permission.POST_NOTIFICATIONS}, 20);
        if (store.paired()) startBridge();
    }

    private TextView text(String value, int size, int color) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color); view.setGravity(Gravity.CENTER); return view;
    }

    private void scan() {
        ScanOptions options = new ScanOptions().setPrompt(getString(R.string.scan_prompt))
            .setBeepEnabled(false).setOrientationLocked(true).setDesiredBarcodeFormats(ScanOptions.QR_CODE);
        scanner.launch(options);
    }

    private void pair(String payload) {
        status.setText(R.string.authenticating_mac);
        Executors.newSingleThreadExecutor().execute(() -> {
            try {
                if (!payload.startsWith("CP1:")) throw new SecurityException("Not a ConnectPhone QR");
                JSONObject offer = new JSONObject(new String(Crypto.unb64(payload.substring(4)), StandardCharsets.UTF_8));
                long now = System.currentTimeMillis() / 1000L;
                long expires = offer.getLong("exp");
                if (offer.getInt("v") != 1 || expires < now || expires > now + 120)
                    throw new SecurityException("Pairing QR expired");
                String host = offer.getString("host"), sid = offer.getString("sid");
                int port = offer.getInt("port"); byte[] pairingSecret = Crypto.unb64(offer.getString("secret"));
                if (host.length() > 253 || port < 1 || port > 65535 || pairingSecret.length != 32 || sid.length() > 64)
                    throw new SecurityException("Invalid pairing offer");
                String device = store.deviceId();
                String nonce = Crypto.b64(new SecureRandom().generateSeed(24));
                String phoneMaterial = "phone-proof\0" + sid + "\0" + device + "\0" + nonce;
                String proof = Crypto.b64(Crypto.hmac(pairingSecret, Crypto.bytes(phoneMaterial)));
                try (Socket socket = new Socket()) {
                    socket.connect(new InetSocketAddress(host, port), 6000);
                    socket.setSoTimeout(8000);
                    BufferedWriter out = new BufferedWriter(new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8));
                    BufferedReader in = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
                    JSONObject hello = new JSONObject().put("type", "pair").put("sid", sid).put("device", device)
                        .put("phone_nonce", nonce).put("proof", proof).put("model", Build.MANUFACTURER + " " + Build.MODEL);
                    out.write(hello.toString()); out.write("\n"); out.flush();
                    JSONObject ack = new JSONObject(in.readLine());
                    String macMaterial = "mac-proof\0" + sid + "\0" + device + "\0" + nonce;
                    String expected = Crypto.b64(Crypto.hmac(pairingSecret, Crypto.bytes(macMaterial)));
                    if (!MessageDigest.isEqual(Crypto.bytes(expected), Crypto.bytes(ack.optString("proof"))))
                        throw new SecurityException("Mac authentication failed");
                }
                byte[] salt = MessageDigest.getInstance("SHA-256").digest(Crypto.bytes(nonce));
                byte[] deviceSecret = Crypto.hkdf(pairingSecret, salt, Crypto.bytes("ConnectPhone companion v1\0" + device));
                store.save(host, port, device, deviceSecret);
                runOnUiThread(() -> { status.setText(R.string.pairing_complete); startBridge(); });
            } catch (Exception error) {
                runOnUiThread(() -> status.setText(getString(R.string.pairing_failed, error.getMessage())));
            }
        });
    }

    private void startBridge() { ContextCompat.startForegroundService(this, new Intent(this, CompanionService.class)); }
}

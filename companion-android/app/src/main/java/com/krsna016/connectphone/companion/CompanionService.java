package com.krsna016.connectphone.companion;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.media.AudioManager;
import android.media.ToneGenerator;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import androidx.core.app.NotificationCompat;
import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

public class CompanionService extends Service {
    private static final String CHANNEL = "connectphone_link";
    private final AtomicBoolean running = new AtomicBoolean();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private Thread worker;
    private ToneGenerator siren;
    private long sendSeq, receiveSeq;
    private volatile Socket activeSocket;

    @Override public void onCreate() {
        super.onCreate();
        NotificationChannel channel = new NotificationChannel(CHANNEL, "Secure Mac connection", NotificationManager.IMPORTANCE_LOW);
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification note = new NotificationCompat.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("ConnectPhone secure link").setContentText("Ready to reconnect to your paired Mac")
            .setOngoing(true).setContentIntent(pending).build();
        startForeground(101, note);
    }

    @Override public int onStartCommand(Intent intent, int flags, int id) {
        if (running.compareAndSet(false, true)) {
            worker = new Thread(this::connectionLoop, "ConnectPhone-Secure-Link"); worker.start();
        }
        return START_STICKY;
    }

    private void connectionLoop() {
        int failures = 0;
        while (running.get()) {
            try {
                JSONObject saved = new SecureStore(this).load();
                if (saved == null) break;
                connect(saved); failures = 0;
            } catch (Exception ignored) {
                failures++;
                try { Thread.sleep(Math.min(30_000L, 1000L << Math.min(failures, 5))); }
                catch (InterruptedException stop) { Thread.currentThread().interrupt(); break; }
            }
        }
        running.set(false);
    }

    private void connect(JSONObject saved) throws Exception {
        String host = saved.getString("host"), device = saved.getString("device");
        byte[] secret = Crypto.unb64(saved.getString("secret"));
        byte[] commandKey = Crypto.hkdf(secret, Crypto.bytes("ConnectPhone-v1"), Crypto.bytes("authenticated-command"));
        byte[] encryptionKey = Crypto.hkdf(secret, Crypto.bytes("ConnectPhone-v1"), Crypto.bytes("encrypted-payload"));
        sendSeq = receiveSeq = 0;
        try (Socket socket = new Socket()) {
            activeSocket = socket;
            socket.connect(new InetSocketAddress(host, saved.getInt("port")), 6000); socket.setKeepAlive(true); socket.setSoTimeout(45_000);
            BufferedWriter out = new BufferedWriter(new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8));
            BufferedReader in = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
            out.write(new JSONObject().put("type", "reconnect").put("device", device).toString()); out.write("\n"); out.flush();
            JSONObject ready = new JSONObject(in.readLine());
            if (!"ready".equals(ready.optString("type"))) throw new SecurityException("Mac rejected reconnect");
            socket.setSoTimeout(30_000);
            sendStatus(out, device, commandKey, encryptionKey);
            while (running.get()) {
                String line;
                try {
                    line = in.readLine();
                } catch (SocketTimeoutException heartbeat) {
                    sendStatus(out, device, commandKey, encryptionKey);
                    continue;
                }
                if (line == null || line.length() > 65536) throw new SecurityException("Connection closed");
                JSONObject command = new JSONObject(line);
                verify(command, device, commandKey, encryptionKey);
                String cap = command.getString("cap");
                if ("alert.start".equals(cap)) startSiren();
                else if ("alert.stop".equals(cap)) stopSiren();
                else throw new SecurityException("Capability rejected");
                sendStatus(out, device, commandKey, encryptionKey);
            }
        } finally {
            activeSocket = null;
        }
    }

    private void sendStatus(BufferedWriter out, String device, byte[] key, byte[] encryptionKey) throws Exception {
        BatteryManager battery = (BatteryManager) getSystemService(BATTERY_SERVICE);
        JSONObject payload = new JSONObject().put("battery", battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY))
            .put("model", Build.MANUFACTURER + " " + Build.MODEL).put("sdk", Build.VERSION.SDK_INT);
        JSONObject header = new JSONObject().put("v", 1).put("device", device).put("cap", "device.status")
            .put("iat", System.currentTimeMillis() / 1000L).put("seq", ++sendSeq)
            .put("nonce", Crypto.b64(new SecureRandom().generateSeed(18)));
        byte[] iv = new SecureRandom().generateSeed(12);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(encryptionKey, "AES"), new GCMParameterSpec(128, iv));
        cipher.updateAAD(Crypto.bytes(Crypto.canonical(header)));
        byte[] ciphertext = cipher.doFinal(Crypto.bytes(Crypto.canonical(payload)));
        JSONObject body = new JSONObject(header.toString()).put("iv", Crypto.b64(iv)).put("ciphertext", Crypto.b64(ciphertext));
        body.put("mac", Crypto.b64(Crypto.hmac(key, Crypto.bytes(Crypto.canonical(body)))));
        out.write(body.toString()); out.write("\n"); out.flush();
    }

    private JSONObject verify(JSONObject message, String device, byte[] key, byte[] encryptionKey) throws Exception {
        if (message.length() != 9 || !message.has("v") || !message.has("device") || !message.has("cap")
            || !message.has("iat") || !message.has("seq") || !message.has("nonce") || !message.has("iv")
            || !message.has("ciphertext") || !message.has("mac")) throw new SecurityException("Unexpected command fields");
        String supplied = message.getString("mac"); JSONObject body = new JSONObject(message.toString()); body.remove("mac");
        String expected = Crypto.b64(Crypto.hmac(key, Crypto.bytes(Crypto.canonical(body))));
        if (!MessageDigest.isEqual(Crypto.bytes(expected), Crypto.bytes(supplied))) throw new SecurityException("Bad command MAC");
        long now = System.currentTimeMillis() / 1000L, issued = body.getLong("iat"), sequence = body.getLong("seq");
        String capability = body.getString("cap");
        if (!("alert.start".equals(capability) || "alert.stop".equals(capability)))
            throw new SecurityException("Capability rejected");
        if (!device.equals(body.getString("device")) || body.getInt("v") != 1 || issued < now - 30 || issued > now + 10 || sequence <= receiveSeq || sequence < 1)
            throw new SecurityException("Stale or replayed command");
        if (Crypto.unb64(body.getString("nonce")).length < 16 || Crypto.unb64(body.getString("iv")).length != 12)
            throw new SecurityException("Invalid command nonce");
        JSONObject header = new JSONObject().put("v", body.getInt("v")).put("device", body.getString("device"))
            .put("cap", body.getString("cap")).put("iat", issued).put("seq", sequence).put("nonce", body.getString("nonce"));
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(encryptionKey, "AES"), new GCMParameterSpec(128, Crypto.unb64(body.getString("iv"))));
        cipher.updateAAD(Crypto.bytes(Crypto.canonical(header)));
        byte[] clear = cipher.doFinal(Crypto.unb64(body.getString("ciphertext")));
        receiveSeq = sequence;
        return new JSONObject(new String(clear, StandardCharsets.UTF_8));
    }

    private void startSiren() {
        handler.post(() -> {
            if (siren == null) siren = new ToneGenerator(AudioManager.STREAM_ALARM, 100);
            siren.startTone(ToneGenerator.TONE_CDMA_ALERT_CALL_GUARD, 30_000);
        });
    }

    private void stopSiren() {
        handler.post(() -> { if (siren != null) siren.stopTone(); });
    }

    @Override public void onDestroy() {
        running.set(false);
        Socket socket = activeSocket;
        if (socket != null) try { socket.close(); } catch (Exception ignored) {}
        if (worker != null) worker.interrupt(); stopSiren();
        if (siren != null) { siren.release(); siren = null; }
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}

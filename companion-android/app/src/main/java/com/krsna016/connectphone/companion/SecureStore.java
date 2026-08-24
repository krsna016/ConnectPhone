package com.krsna016.connectphone.companion;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.UUID;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class SecureStore {
    private static final String ALIAS = "ConnectPhoneCompanionKey";
    private final SharedPreferences prefs;

    SecureStore(Context context) { prefs = context.getSharedPreferences("secure-pairing", Context.MODE_PRIVATE); }

    String deviceId() {
        String value = prefs.getString("device-id", null);
        if (value != null) return value;
        value = UUID.randomUUID().toString();
        if (!prefs.edit().putString("device-id", value).commit())
            throw new IllegalStateException("Could not store companion identity");
        return value;
    }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(ALIAS)) return ((KeyStore.SecretKeyEntry) store.getEntry(ALIAS, null)).getSecretKey();
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS,
            KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256).build());
        return generator.generateKey();
    }

    void save(String host, int port, String device, byte[] secret) throws Exception {
        JSONObject clear = new JSONObject().put("host", host).put("port", port)
            .put("device", device).put("secret", Crypto.b64(secret));
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] encrypted = cipher.doFinal(clear.toString().getBytes(StandardCharsets.UTF_8));
        prefs.edit().putString("iv", Crypto.b64(cipher.getIV())).putString("data", Crypto.b64(encrypted)).apply();
    }

    JSONObject load() throws Exception {
        String iv = prefs.getString("iv", null), data = prefs.getString("data", null);
        if (iv == null || data == null) return null;
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Crypto.unb64(iv)));
        return new JSONObject(new String(cipher.doFinal(Crypto.unb64(data)), StandardCharsets.UTF_8));
    }

    boolean paired() {
        try { return load() != null; } catch (Exception ignored) { return false; }
    }

    void clear() {
        prefs.edit().clear().apply();
        try {
            KeyStore store = KeyStore.getInstance("AndroidKeyStore");
            store.load(null);
            if (store.containsAlias(ALIAS)) store.deleteEntry(ALIAS);
        } catch (Exception ignored) {}
    }
}

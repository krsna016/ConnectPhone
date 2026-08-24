package com.krsna016.connectphone.companion;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;

import java.nio.charset.StandardCharsets;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public class CryptoTest {
    private static byte[] hex(String value) {
        byte[] out = new byte[value.length() / 2];
        for (int i = 0; i < out.length; i++)
            out[i] = (byte) Integer.parseInt(value.substring(i * 2, i * 2 + 2), 16);
        return out;
    }

    @Test public void hkdfMatchesDesktopProtocolVectors() throws Exception {
        byte[] secret = new byte[32];
        java.util.Arrays.fill(secret, (byte) 's');
        assertArrayEquals(
            hex("dc31fae7b367ee0fe06ce0c9631f6e87361a28743d8d2eebe1cba5f8131944ea"),
            Crypto.hkdf(secret, Crypto.bytes("ConnectPhone-v1"), Crypto.bytes("authenticated-command"))
        );
        assertArrayEquals(
            hex("1d374d5fed41d3a106d55479cdaf2c9cddfee5631aec87abe1c02e748b015dcf"),
            Crypto.hkdf(secret, Crypto.bytes("ConnectPhone-v1"), Crypto.bytes("encrypted-payload"))
        );
    }

    @Test public void canonicalJsonIsStableAndBase64IsUrlSafe() throws Exception {
        JSONObject value = new JSONObject()
            .put("z", new JSONArray().put(true).put("text"))
            .put("a", new JSONObject().put("b", 2).put("a", 1));
        assertEquals("{\"a\":{\"a\":1,\"b\":2},\"z\":[true,\"text\"]}", Crypto.canonical(value));
        byte[] raw = "companion-protocol".getBytes(StandardCharsets.UTF_8);
        assertArrayEquals(raw, Crypto.unb64(Crypto.b64(raw)));
    }
}

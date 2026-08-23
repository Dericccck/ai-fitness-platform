package com.shuyiwa.fitness.gateway.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import org.junit.Test;

import java.math.BigInteger;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PublicKey;
import java.util.Base64;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;

public class JwksPublicKeyProviderTest {

    @Test
    public void parsesRsaJwksAndReusesValidCache() throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
        generator.initialize(2048);
        KeyPair keyPair = generator.generateKeyPair();
        Map<String, Object> jwk = new HashMap<>();
        jwk.put("kid", "rsa-v1");
        jwk.put("kty", "RSA");
        jwk.put("alg", "RS256");
        jwk.put("use", "sig");
        java.security.interfaces.RSAPublicKey publicKey =
                (java.security.interfaces.RSAPublicKey) keyPair.getPublic();
        jwk.put("n", base64Url(publicKey.getModulus()));
        jwk.put("e", base64Url(publicKey.getPublicExponent()));
        Map<String, Object> document = new HashMap<>();
        document.put("keys", Collections.singletonList(jwk));
        String body = new ObjectMapper().writeValueAsString(document);
        AtomicInteger requests = new AtomicInteger();

        GatewayProperties properties = new GatewayProperties();
        properties.setContextVerificationJwksUrl("https://issuer.test/jwks");
        JwksPublicKeyProvider provider = new JwksPublicKeyProvider(
                new ObjectMapper(), properties
        ) {
            @Override
            protected String fetchJwksDocument(String jwksUrl) {
                requests.incrementAndGet();
                return body;
            }
        };
        PublicKey first = provider.getPublicKey("rsa-v1");
        PublicKey second = provider.getPublicKey("rsa-v1");

        assertNotNull(first);
        assertEquals(first, second);
        assertEquals(1, requests.get());
    }

    private static String base64Url(BigInteger value) {
        byte[] bytes = value.toByteArray();
        if (bytes.length > 1 && bytes[0] == 0) {
            byte[] unsigned = new byte[bytes.length - 1];
            System.arraycopy(bytes, 1, unsigned, 0, unsigned.length);
            bytes = unsigned;
        }
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
}

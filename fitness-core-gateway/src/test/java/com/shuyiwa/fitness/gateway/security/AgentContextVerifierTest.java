package com.shuyiwa.fitness.gateway.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import org.junit.Test;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

public class AgentContextVerifierTest {

    private static final Instant NOW = Instant.parse("2026-08-12T00:00:00Z");

    @Test
    public void acceptsSignedContextWithinConfiguredLifetime() throws Exception {
        GatewayProperties properties = properties();
        AgentContextVerifier verifier = new AgentContextVerifier(
                new ObjectMapper(), properties, Clock.fixed(NOW, ZoneOffset.UTC)
        );

        AgentContext context = verifier.verify(token(NOW.minusSeconds(10), NOW.plusSeconds(120)));

        assertEquals("user-1", context.getSubjectUserId());
        assertEquals("org-1", context.getOrganizationIds().iterator().next());
        assertEquals("STUDENT", context.getRoles().iterator().next());
        assertEquals("KNOWLEDGE_REVIEW_FITNESS", context.getCapabilities().iterator().next());
        assertEquals("COACH_CERTIFIED", context.getQualifications().iterator().next());
    }

    @Test(expected = GatewaySecurityException.class)
    public void rejectsExpiredContext() throws Exception {
        AgentContextVerifier verifier = new AgentContextVerifier(
                new ObjectMapper(), properties(), Clock.fixed(NOW, ZoneOffset.UTC)
        );

        verifier.verify(token(NOW.minusSeconds(120), NOW.minusSeconds(1)));
    }

    @Test
    public void rejectsTamperedPayload() throws Exception {
        AgentContextVerifier verifier = new AgentContextVerifier(
                new ObjectMapper(), properties(), Clock.fixed(NOW, ZoneOffset.UTC)
        );
        String valid = token(NOW.minusSeconds(10), NOW.plusSeconds(120));
        String tampered = valid.substring(0, valid.length() - 1) + (valid.endsWith("A") ? "B" : "A");

        try {
            verifier.verify(tampered);
        } catch (GatewaySecurityException expected) {
            return;
        }
        throw new AssertionError("tampered context must be rejected");
    }

    @Test
    public void acceptsVersionedContextWithRotatedVerificationKey() throws Exception {
        GatewayProperties properties = properties();
        properties.setContextSigningKeyId("v2");
        Map<String, String> keyRing = new HashMap<>();
        keyRing.put("v1", "retired-context-secret");
        properties.setContextSigningKeyRing(keyRing);

        AgentContextVerifier verifier = new AgentContextVerifier(
                new ObjectMapper(), properties, Clock.fixed(NOW, ZoneOffset.UTC)
        );

        AgentContext context = verifier.verify(token(
                NOW.minusSeconds(10), NOW.plusSeconds(120), "HS256", "v1", "retired-context-secret"
        ));

        assertEquals("user-1", context.getSubjectUserId());
    }

    @Test(expected = GatewaySecurityException.class)
    public void rejectsUnknownAlgorithmBeforeBusinessClaimsAreTrusted() throws Exception {
        AgentContextVerifier verifier = new AgentContextVerifier(
                new ObjectMapper(), properties(), Clock.fixed(NOW, ZoneOffset.UTC)
        );

        verifier.verify(token(NOW.minusSeconds(10), NOW.plusSeconds(120), "RS256", "legacy", "test-context-secret"));
    }

    @Test
    public void rejectsUnknownKeyIdDuringRotation() throws Exception {
        AgentContextVerifier verifier = new AgentContextVerifier(
                new ObjectMapper(), properties(), Clock.fixed(NOW, ZoneOffset.UTC)
        );

        try {
            verifier.verify(token(
                    NOW.minusSeconds(10), NOW.plusSeconds(120), "HS256", "deleted-key", "test-context-secret"
            ));
            fail("unknown key id must be rejected");
        } catch (GatewaySecurityException expected) {
            // 未配置的 kid 不能回退到当前主密钥。
        }
    }

    private static GatewayProperties properties() {
        GatewayProperties properties = new GatewayProperties();
        properties.setContextSigningSecret("test-context-secret");
        properties.setMaxContextTtlSeconds(300);
        return properties;
    }

    private static String token(Instant issuedAt, Instant expiresAt) throws Exception {
        return token(issuedAt, expiresAt, null, null, "test-context-secret");
    }

    private static String token(
            Instant issuedAt,
            Instant expiresAt,
            String algorithm,
            String keyId,
            String secret
    ) throws Exception {
        Map<String, Object> payload = new HashMap<>();
        if (algorithm != null) {
            payload.put("alg", algorithm);
        }
        if (keyId != null) {
            payload.put("kid", keyId);
        }
        payload.put("sub", "user-1");
        payload.put("orgs", new String[]{"org-1"});
        payload.put("roles", new String[]{"STUDENT"});
        payload.put("capabilities", new String[]{"KNOWLEDGE_REVIEW_FITNESS"});
        payload.put("qualifications", new String[]{"COACH_CERTIFIED"});
        payload.put("iat", issuedAt.getEpochSecond());
        payload.put("exp", expiresAt.getEpochSecond());
        payload.put("nonce", "nonce-1");
        byte[] payloadBytes = new ObjectMapper().writeValueAsBytes(payload);
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
        return encoder.encodeToString(payloadBytes) + "." + encoder.encodeToString(mac.doFinal(payloadBytes));
    }
}

package com.shuyiwa.fitness.gateway.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import org.junit.Test;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;

import static org.junit.Assert.assertEquals;

/** Gateway v2 预留字段和业务范围绑定测试。 */
public class ConfirmationTokenVerifierTest {

    private static final String SECRET = "confirmation-secret-for-test-32-bytes";

    @Test
    public void acceptsTokenWithCompleteScope() throws Exception {
        ConfirmationTokenVerifier verifier = new ConfirmationTokenVerifier(
                new ObjectMapper(), properties()
        );
        ConfirmationTokenClaims claims = verifier.verify(
                token("org-1", "fitness.training.plan.create_draft.v1"),
                context(),
                "fitness.training.plan.create_draft.v1",
                "CREATE_TRAINING_DRAFT",
                "org-1:student-1",
                "request-1"
        );

        assertEquals("confirmation-1", claims.getConfirmationId());
        assertEquals("jti-1", claims.getJti());
        assertEquals("org-1", claims.getOrganizationId());
    }

    @Test(expected = GatewaySecurityException.class)
    public void rejectsToolIdTampering() throws Exception {
        new ConfirmationTokenVerifier(new ObjectMapper(), properties()).verify(
                token("org-1", "fitness.training.plan.create_draft.v1"),
                context(),
                "fitness.training.plan.publish.v1",
                "CREATE_TRAINING_DRAFT",
                "org-1:student-1",
                "request-1"
        );
    }

    @Test(expected = GatewaySecurityException.class)
    public void rejectsOrganizationOutsideSignedScope() throws Exception {
        new ConfirmationTokenVerifier(new ObjectMapper(), properties()).verify(
                token("org-2", "fitness.training.plan.create_draft.v1"),
                context(),
                "fitness.training.plan.create_draft.v1",
                "CREATE_TRAINING_DRAFT",
                "org-2:student-1",
                "request-1"
        );
    }

    private static GatewayProperties properties() {
        GatewayProperties properties = new GatewayProperties();
        properties.setConfirmationSigningSecret(SECRET);
        return properties;
    }

    private static AgentContext context() {
        return new AgentContext(
                "coach-1",
                java.util.Collections.singleton("org-1"),
                java.util.Collections.singleton(AgentContext.ROLE_COACH),
                Instant.parse("2026-08-13T00:00:00Z"),
                Instant.parse("2026-08-13T00:05:00Z"),
                "nonce"
        );
    }

    private static String token(String organizationId, String toolId) throws Exception {
        Map<String, Object> payload = new HashMap<>();
        payload.put("sub", "coach-1");
        payload.put("action", "CREATE_TRAINING_DRAFT");
        payload.put("resource", organizationId + ":student-1");
        payload.put("request_id", "request-1");
        payload.put("exp", Instant.now().plusSeconds(120).getEpochSecond());
        payload.put("confirmation_id", "confirmation-1");
        payload.put("tool_id", toolId);
        payload.put("organization_id", organizationId);
        payload.put("payload_hash", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        payload.put("jti", "jti-1");
        byte[] payloadBytes = new ObjectMapper().writeValueAsBytes(payload);
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
        return encoder.encodeToString(payloadBytes) + "." + encoder.encodeToString(mac.doFinal(payloadBytes));
    }
}

package com.shuyiwa.fitness.gateway.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.Map;

/**
 * 验证高风险写操作的确认凭证。
 *
 * <p>确认凭证由受信任的确认服务签发，绑定用户、动作、工具、机构、资源、参数哈希、
 * 一次性 JTI 和过期时间。它不是普通 Header 字符串，也不能由模型临时生成；Gateway
 * 验证失败时直接拒绝写操作。</p>
 */
@Component
public class ConfirmationTokenVerifier {

    private final ObjectMapper objectMapper;
    private final GatewayProperties properties;

    public ConfirmationTokenVerifier(ObjectMapper objectMapper, GatewayProperties properties) {
        this.objectMapper = objectMapper;
        this.properties = properties;
    }

    public ConfirmationTokenClaims verify(String token, AgentContext context, String toolId,
                                          String action, String resource, String requestId) {
        if (token == null || token.trim().isEmpty()
                || properties.getConfirmationSigningSecret().trim().isEmpty()) {
            throw new GatewaySecurityException("confirmation token is required");
        }
        try {
            String[] parts = token.split("\\.", -1);
            if (parts.length != 2) {
                throw new GatewaySecurityException("invalid confirmation token");
            }
            byte[] payloadBytes = Base64.getUrlDecoder().decode(parts[0]);
            byte[] signature = Base64.getUrlDecoder().decode(parts[1]);
            byte[] expected = hmac(payloadBytes, properties.getConfirmationSigningSecret());
            if (!java.security.MessageDigest.isEqual(expected, signature)) {
                throw new GatewaySecurityException("invalid confirmation token");
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> payload = objectMapper.readValue(payloadBytes, Map.class);
            String subjectUserId = string(payload, "sub");
            String tokenAction = string(payload, "action");
            String tokenResource = string(payload, "resource");
            String tokenRequestId = string(payload, "request_id");
            String tokenToolId = string(payload, "tool_id");
            String organizationId = string(payload, "organization_id");
            String confirmationId = string(payload, "confirmation_id");
            String payloadHash = string(payload, "payload_hash");
            String jti = string(payload, "jti");
            if (!context.getSubjectUserId().equals(subjectUserId)
                    || !toolId.equals(tokenToolId)
                    || !action.equals(tokenAction)
                    || !resource.equals(tokenResource)
                    || !requestId.equals(tokenRequestId)
                    || !context.canAccessOrganization(organizationId)
                    || !payloadHash.matches("[0-9a-fA-F]{64}")) {
                throw new GatewaySecurityException("confirmation token scope mismatch");
            }
            long exp = Long.parseLong(string(payload, "exp"));
            if (exp <= Instant.now().getEpochSecond()) {
                throw new GatewaySecurityException("confirmation token expired");
            }
            return new ConfirmationTokenClaims(
                    confirmationId, tokenToolId, tokenAction, subjectUserId, organizationId,
                    tokenResource, tokenRequestId, payloadHash, jti, exp
            );
        } catch (GatewaySecurityException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new GatewaySecurityException("invalid confirmation token");
        }
    }

    private static byte[] hmac(byte[] payload, String secret) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        return mac.doFinal(payload);
    }

    private static String string(Map<String, Object> payload, String key) {
        Object value = payload.get(key);
        if (value == null || value.toString().trim().isEmpty()) {
            throw new GatewaySecurityException("confirmation token field is missing");
        }
        return value.toString();
    }
}

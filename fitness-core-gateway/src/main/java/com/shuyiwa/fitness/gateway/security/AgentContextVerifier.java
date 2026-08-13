package com.shuyiwa.fitness.gateway.security;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.util.Base64;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;

/**
 * 验证签名 AgentContext。
 *
 * <p>Token 格式为 {@code base64url(payload).base64url(HMAC-SHA256(payload))}。这里只接受
 * 明确的 subject、组织范围、角色、签发时间、过期时间和 nonce；不接受客户端自定义的
 * 任意 claims 作为授权依据。上下文有效期默认只有 5 分钟，降低泄露后的可利用窗口。</p>
 */
@Component
public class AgentContextVerifier {

    private static final String HMAC_ALGORITHM = "HmacSHA256";
    private static final int MAX_TOKEN_LENGTH = 8192;
    private final ObjectMapper objectMapper;
    private final GatewayProperties properties;
    private final Clock clock;

    public AgentContextVerifier(ObjectMapper objectMapper, GatewayProperties properties) {
        this(objectMapper, properties, Clock.systemUTC());
    }

    AgentContextVerifier(ObjectMapper objectMapper, GatewayProperties properties, Clock clock) {
        this.objectMapper = objectMapper;
        this.properties = properties;
        this.clock = clock;
    }

    public AgentContext verify(String token) {
        if (token == null || token.isEmpty() || token.length() > MAX_TOKEN_LENGTH) {
            throw new GatewaySecurityException("invalid agent context");
        }
        String[] parts = token.split("\\.", -1);
        if (parts.length != 2) {
            throw new GatewaySecurityException("invalid agent context format");
        }

        byte[] payload;
        byte[] signature;
        try {
            Base64.Decoder decoder = Base64.getUrlDecoder();
            payload = decoder.decode(parts[0]);
            signature = decoder.decode(parts[1]);
        } catch (IllegalArgumentException exception) {
            throw new GatewaySecurityException("invalid agent context encoding");
        }

        byte[] expectedSignature = sign(payload);
        if (!MessageDigest.isEqual(expectedSignature, signature)) {
            throw new GatewaySecurityException("invalid agent context signature");
        }

        try {
            JsonNode root = objectMapper.readTree(payload);
            String subject = requiredText(root, "sub");
            String nonce = requiredText(root, "nonce");
            Set<String> organizationIds = requiredStringSet(root, "orgs");
            Set<String> roles = requiredStringSet(root, "roles");
            // 为兼容尚未承担专业审核的旧客户端，这两个签名 claim 可以缺省；
            // 缺省只代表空集合，绝不会赋予任何审核能力或资质。
            Set<String> capabilities = optionalStringSet(root, "capabilities");
            Set<String> qualifications = optionalStringSet(root, "qualifications");
            Instant issuedAt = epochSeconds(root, "iat");
            Instant expiresAt = epochSeconds(root, "exp");
            Instant now = clock.instant();

            if (expiresAt.isBefore(issuedAt)
                    || expiresAt.isAfter(issuedAt.plusSeconds(properties.getMaxContextTtlSeconds()))
                    || issuedAt.isAfter(now.plusSeconds(30))
                    || !expiresAt.isAfter(now)) {
                throw new GatewaySecurityException("expired or invalid agent context");
            }
            return new AgentContext(
                    subject, organizationIds, roles, capabilities, qualifications,
                    issuedAt, expiresAt, nonce
            );
        } catch (GatewaySecurityException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new GatewaySecurityException("invalid agent context claims");
        }
    }

    private byte[] sign(byte[] payload) {
        String secret = properties.getContextSigningSecret();
        if (secret == null || secret.isEmpty()) {
            throw new GatewaySecurityException("agent context verifier is not configured");
        }
        try {
            Mac mac = Mac.getInstance(HMAC_ALGORITHM);
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), HMAC_ALGORITHM));
            return mac.doFinal(payload);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot initialize agent context verifier", exception);
        }
    }

    private static String requiredText(JsonNode root, String field) {
        JsonNode value = root.get(field);
        if (value == null || !value.isTextual() || value.asText().isEmpty()) {
            throw new GatewaySecurityException("missing agent context field: " + field);
        }
        return value.asText();
    }

    private static Set<String> requiredStringSet(JsonNode root, String field) {
        JsonNode values = root.get(field);
        if (values == null || !values.isArray() || values.size() == 0) {
            throw new GatewaySecurityException("missing agent context field: " + field);
        }
        Set<String> result = new HashSet<>();
        Iterator<JsonNode> iterator = values.elements();
        while (iterator.hasNext()) {
            JsonNode value = iterator.next();
            if (!value.isTextual() || value.asText().isEmpty()) {
                throw new GatewaySecurityException("invalid agent context field: " + field);
            }
            result.add(value.asText());
        }
        return result;
    }

    private static Set<String> optionalStringSet(JsonNode root, String field) {
        JsonNode values = root.get(field);
        if (values == null) {
            return new HashSet<>();
        }
        if (!values.isArray()) {
            throw new GatewaySecurityException("invalid agent context field: " + field);
        }
        Set<String> result = new HashSet<>();
        Iterator<JsonNode> iterator = values.elements();
        while (iterator.hasNext()) {
            JsonNode value = iterator.next();
            if (!value.isTextual() || value.asText().isEmpty()) {
                throw new GatewaySecurityException("invalid agent context field: " + field);
            }
            result.add(value.asText());
        }
        return result;
    }

    private static Instant epochSeconds(JsonNode root, String field) {
        JsonNode value = root.get(field);
        if (value == null || !value.isIntegralNumber()) {
            throw new GatewaySecurityException("missing agent context field: " + field);
        }
        return Instant.ofEpochSecond(value.asLong());
    }
}

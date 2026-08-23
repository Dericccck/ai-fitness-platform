package com.shuyiwa.fitness.gateway.security;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.time.Clock;
import java.time.Instant;
import java.util.Base64;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;

/**
 * 验证签名 AgentContext。
 *
 * <p>Token 格式为 {@code base64url(payload).base64url(signature)}。v1 使用 HMAC-SHA256，
 * v2 支持 RS256；这里只接受明确的 subject、组织范围、角色、签发时间、过期时间和 nonce，
 * 不接受客户端自定义的任意 claims 作为授权依据。上下文有效期默认只有 5 分钟，降低
 * 泄露后的可利用窗口。生产环境的 RS256 私钥只应保留在认证服务，Gateway 只持有公钥。</p>
 */
@Component
public class AgentContextVerifier {

    private static final String HMAC_ALGORITHM = "HmacSHA256";
    private static final String TOKEN_ALGORITHM = "HS256";
    private static final String RSA_TOKEN_ALGORITHM = "RS256";
    private static final String LEGACY_KEY_ID = "legacy";
    private static final int MAX_TOKEN_LENGTH = 8192;
    private final ObjectMapper objectMapper;
    private final GatewayProperties properties;
    private final Clock clock;
    private final AgentContextPublicKeyProvider publicKeyProvider;

    @Autowired
    public AgentContextVerifier(ObjectMapper objectMapper, GatewayProperties properties) {
        this(objectMapper, properties, Clock.systemUTC(), new JwksPublicKeyProvider(objectMapper, properties));
    }

    AgentContextVerifier(ObjectMapper objectMapper, GatewayProperties properties, Clock clock) {
        this(objectMapper, properties, clock, new JwksPublicKeyProvider(objectMapper, properties));
    }

    AgentContextVerifier(
            ObjectMapper objectMapper,
            GatewayProperties properties,
            Clock clock,
            AgentContextPublicKeyProvider publicKeyProvider
    ) {
        this.objectMapper = objectMapper;
        this.properties = properties;
        this.clock = clock;
        this.publicKeyProvider = publicKeyProvider;
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

        JsonNode root;
        try {
            root = objectMapper.readTree(payload);
            if (root == null || !root.isObject()) {
                throw new GatewaySecurityException("invalid agent context claims");
            }
        } catch (GatewaySecurityException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new GatewaySecurityException("invalid agent context claims");
        }

        // alg/kid 属于签名载荷的一部分，必须在验签前用于选择验证策略；选择失败时直接拒绝，
        // 不允许调用方通过伪造算法名称或未知 kid 触发降级到任意默认密钥。
        String algorithm = optionalText(root, "alg", TOKEN_ALGORITHM);
        String keyId = optionalText(root, "kid", LEGACY_KEY_ID);
        validateSigningContract(algorithm, keyId);

        if (!verifySignature(payload, signature, algorithm, keyId)) {
            throw new GatewaySecurityException("invalid agent context signature");
        }

        try {
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

    private boolean verifySignature(byte[] payload, byte[] signature, String algorithm, String keyId) {
        if (TOKEN_ALGORITHM.equals(algorithm)) {
            byte[] expectedSignature = signHmac(payload, keyId);
            return MessageDigest.isEqual(expectedSignature, signature);
        }
        if (RSA_TOKEN_ALGORITHM.equals(algorithm)) {
            return verifyRsa(payload, signature, keyId);
        }
        throw new GatewaySecurityException("unsupported agent context signing contract");
    }

    private byte[] signHmac(byte[] payload, String keyId) {
        String secret = resolveSigningSecret(keyId);
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

    private boolean verifyRsa(byte[] payload, byte[] signature, String keyId) {
        PublicKey publicKey = resolveStaticPublicKey(keyId);
        if (publicKey == null) {
            publicKey = publicKeyProvider.getPublicKey(keyId);
        }
        if (publicKey == null) {
            throw new GatewaySecurityException("agent context verification key is not configured");
        }
        try {
            Signature verifier = Signature.getInstance("SHA256withRSA");
            verifier.initVerify(publicKey);
            verifier.update(payload);
            return verifier.verify(signature);
        } catch (Exception exception) {
            // 公钥格式错误、算法不匹配和验签失败统一 fail-closed，避免泄露密钥配置细节。
            throw new GatewaySecurityException("invalid agent context verification key");
        }
    }

    private PublicKey resolveStaticPublicKey(String keyId) {
        String publicKeyPem = properties.getContextVerificationPublicKeyRing().get(keyId);
        if (publicKeyPem == null || publicKeyPem.trim().isEmpty()) {
            return null;
        }
        try {
            byte[] der = Base64.getMimeDecoder().decode(
                    publicKeyPem
                            .replace("-----BEGIN PUBLIC KEY-----", "")
                            .replace("-----END PUBLIC KEY-----", "")
            );
            return KeyFactory.getInstance("RSA")
                    .generatePublic(new X509EncodedKeySpec(der));
        } catch (Exception exception) {
            throw new GatewaySecurityException("invalid agent context verification key");
        }
    }

    private void validateSigningContract(String algorithm, String keyId) {
        String configuredAlgorithm = properties.getContextSigningAlgorithm();
        if (!(TOKEN_ALGORITHM.equals(algorithm) || RSA_TOKEN_ALGORITHM.equals(algorithm))
                || !(TOKEN_ALGORITHM.equals(configuredAlgorithm)
                || RSA_TOKEN_ALGORITHM.equals(configuredAlgorithm))
                || !algorithm.equals(configuredAlgorithm)
                || keyId.trim().isEmpty()) {
            throw new GatewaySecurityException("unsupported agent context signing contract");
        }
        String activeKeyId = properties.getContextSigningKeyId();
        if (activeKeyId == null || activeKeyId.trim().isEmpty()) {
            throw new GatewaySecurityException("agent context key id is not configured");
        }
    }

    private String resolveSigningSecret(String keyId) {
        if (keyId.equals(properties.getContextSigningKeyId())) {
            return properties.getContextSigningSecret();
        }
        Map<String, String> keyRing = properties.getContextSigningKeyRing();
        if (keyRing != null) {
            return keyRing.get(keyId);
        }
        return null;
    }

    private static String optionalText(JsonNode root, String field, String defaultValue) {
        JsonNode value = root.get(field);
        if (value == null) {
            return defaultValue;
        }
        if (!value.isTextual() || value.asText().trim().isEmpty()) {
            throw new GatewaySecurityException("invalid agent context field: " + field);
        }
        return value.asText();
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

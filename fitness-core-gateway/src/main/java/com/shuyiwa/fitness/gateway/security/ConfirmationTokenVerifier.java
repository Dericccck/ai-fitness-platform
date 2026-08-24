package com.shuyiwa.fitness.gateway.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
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

    private static final String HMAC_TOKEN_ALGORITHM = "HS256";
    private static final String RSA_TOKEN_ALGORITHM = "RS256";
    private static final String LEGACY_KEY_ID = "legacy";
    private final ObjectMapper objectMapper;
    private final GatewayProperties properties;
    private final AgentContextPublicKeyProvider publicKeyProvider;

    /**
     * Spring 5.1 在类同时存在 public 和 package-private 构造函数时，不能可靠地推断
     * 应该使用哪一个构造函数；如果不显式标注，启动阶段会退回寻找无参构造函数，最终
     * 以 ``NoSuchMethodException`` 失败。这里明确指定生产构造函数，同时保留带公钥
     * Provider 的包级构造函数供单元测试注入可控依赖。
     */
    @Autowired
    public ConfirmationTokenVerifier(ObjectMapper objectMapper, GatewayProperties properties) {
        this(
                objectMapper,
                properties,
                new JwksPublicKeyProvider(
                        objectMapper,
                        properties.getConfirmationVerificationJwksUrl(),
                        properties.getConfirmationVerificationJwksCacheSeconds(),
                        properties.getConfirmationVerificationJwksTimeoutMilliseconds()
                )
        );
    }

    ConfirmationTokenVerifier(
            ObjectMapper objectMapper,
            GatewayProperties properties,
            AgentContextPublicKeyProvider publicKeyProvider
    ) {
        this.objectMapper = objectMapper;
        this.properties = properties;
        this.publicKeyProvider = publicKeyProvider;
    }

    public ConfirmationTokenClaims verify(String token, AgentContext context, String toolId,
                                          String action, String resource, String requestId) {
        if (token == null || token.trim().isEmpty()) {
            throw new GatewaySecurityException("confirmation token is required");
        }
        try {
            String[] parts = token.split("\\.", -1);
            if (parts.length != 2) {
                throw new GatewaySecurityException("invalid confirmation token");
            }
            byte[] payloadBytes = Base64.getUrlDecoder().decode(parts[0]);
            byte[] signature = Base64.getUrlDecoder().decode(parts[1]);
            @SuppressWarnings("unchecked")
            Map<String, Object> payload = objectMapper.readValue(payloadBytes, Map.class);
            String algorithm = optionalString(payload, "alg", HMAC_TOKEN_ALGORITHM);
            String keyId = optionalString(payload, "kid", LEGACY_KEY_ID);
            verifySignature(payloadBytes, signature, algorithm, keyId);
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

    private void verifySignature(byte[] payload, byte[] signature, String algorithm, String keyId)
            throws Exception {
        String configuredAlgorithm = properties.getConfirmationSigningAlgorithm();
        if (!(HMAC_TOKEN_ALGORITHM.equals(algorithm) || RSA_TOKEN_ALGORITHM.equals(algorithm))
                || !algorithm.equals(configuredAlgorithm)
                || keyId.trim().isEmpty()) {
            throw new GatewaySecurityException("unsupported confirmation token signing contract");
        }
        if (HMAC_TOKEN_ALGORITHM.equals(algorithm)) {
            String secret = resolveSigningSecret(keyId);
            if (secret == null || secret.trim().isEmpty()) {
                throw new GatewaySecurityException("confirmation signing key is not configured");
            }
            byte[] expected = hmac(payload, secret);
            if (!java.security.MessageDigest.isEqual(expected, signature)) {
                throw new GatewaySecurityException("invalid confirmation token");
            }
            return;
        }

        PublicKey publicKey = resolveStaticPublicKey(keyId);
        if (publicKey == null) {
            publicKey = publicKeyProvider.getPublicKey(keyId);
        }
        if (publicKey == null) {
            throw new GatewaySecurityException("confirmation verification key is not configured");
        }
        Signature verifier = Signature.getInstance("SHA256withRSA");
        verifier.initVerify(publicKey);
        verifier.update(payload);
        if (!verifier.verify(signature)) {
            throw new GatewaySecurityException("invalid confirmation token");
        }
    }

    private String resolveSigningSecret(String keyId) {
        if (keyId.equals(properties.getConfirmationSigningKeyId())) {
            return properties.getConfirmationSigningSecret();
        }
        Map<String, String> keyRing = properties.getConfirmationSigningKeyRing();
        return keyRing == null ? null : keyRing.get(keyId);
    }

    private PublicKey resolveStaticPublicKey(String keyId) {
        String publicKeyPem = properties.getConfirmationVerificationPublicKeyRing().get(keyId);
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
            throw new GatewaySecurityException("invalid confirmation verification key");
        }
    }

    private static String optionalString(Map<String, Object> payload, String key, String defaultValue) {
        Object value = payload.get(key);
        return value == null ? defaultValue : string(payload, key);
    }

    private static String string(Map<String, Object> payload, String key) {
        Object value = payload.get(key);
        if (value == null || value.toString().trim().isEmpty()) {
            throw new GatewaySecurityException("confirmation token field is missing");
        }
        return value.toString();
    }
}

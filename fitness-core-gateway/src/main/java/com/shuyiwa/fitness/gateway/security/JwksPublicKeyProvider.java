package com.shuyiwa.fitness.gateway.security;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.web.client.RestTemplate;

import java.math.BigInteger;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.spec.RSAPublicKeySpec;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

/**
 * 从标准 JWKS 文档读取 RSA 公钥并按 kid 缓存。
 *
 * <p>缓存有效期内不重复访问认证服务；缓存过期后的刷新失败会直接抛出安全异常，
 * 不使用旧缓存继续放行，避免认证服务撤销或轮换后 Gateway 长时间信任过期公钥。</p>
 */
public class JwksPublicKeyProvider implements AgentContextPublicKeyProvider {

    private static final String RSA_KEY_TYPE = "RSA";
    private static final String RSA_SIGNATURE_ALGORITHM = "RS256";
    private static final int MAX_KEYS = 50;
    private final ObjectMapper objectMapper;
    private final GatewayProperties properties;
    private final RestTemplate restTemplate;
    private final Clock clock;
    private volatile CacheSnapshot cache;

    public JwksPublicKeyProvider(ObjectMapper objectMapper, GatewayProperties properties) {
        this(
                objectMapper,
                properties,
                new RestTemplateBuilder()
                        .setConnectTimeout(Duration.ofMillis(properties.getContextVerificationJwksTimeoutMilliseconds()))
                        .setReadTimeout(Duration.ofMillis(properties.getContextVerificationJwksTimeoutMilliseconds()))
                        .build(),
                Clock.systemUTC()
        );
    }

    JwksPublicKeyProvider(
            ObjectMapper objectMapper,
            GatewayProperties properties,
            RestTemplate restTemplate,
            Clock clock
    ) {
        this.objectMapper = objectMapper;
        this.properties = properties;
        this.restTemplate = restTemplate;
        this.clock = clock;
    }

    @Override
    public PublicKey getPublicKey(String keyId) {
        String jwksUrl = properties.getContextVerificationJwksUrl();
        if (jwksUrl == null || jwksUrl.trim().isEmpty()) {
            return null;
        }
        CacheSnapshot current = cache;
        Instant now = clock.instant();
        if (current == null || !now.isBefore(current.expiresAt)) {
            synchronized (this) {
                current = cache;
                if (current == null || !now.isBefore(current.expiresAt)) {
                    current = refresh(jwksUrl, now);
                    cache = current;
                }
            }
        }
        return current.keys.get(keyId);
    }

    private CacheSnapshot refresh(String jwksUrl, Instant now) {
        try {
            String document = fetchJwksDocument(jwksUrl);
            Map<String, PublicKey> keys = parse(document);
            return new CacheSnapshot(
                    Collections.unmodifiableMap(keys),
                    now.plusSeconds(properties.getContextVerificationJwksCacheSeconds())
            );
        } catch (GatewaySecurityException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new GatewaySecurityException("agent context JWKS is unavailable");
        }
    }

    /** 抽出的网络边界，生产使用 RestTemplate，单元测试可注入内存文档而不监听端口。 */
    protected String fetchJwksDocument(String jwksUrl) {
        return restTemplate.getForObject(jwksUrl, String.class);
    }

    private Map<String, PublicKey> parse(String document) {
        if (document == null || document.isEmpty()) {
            throw new GatewaySecurityException("invalid agent context JWKS");
        }
        try {
            JsonNode root = objectMapper.readTree(document);
            JsonNode keysNode = root == null ? null : root.get("keys");
            if (keysNode == null || !keysNode.isArray() || keysNode.size() > MAX_KEYS) {
                throw new GatewaySecurityException("invalid agent context JWKS");
            }
            Map<String, PublicKey> keys = new HashMap<>();
            for (JsonNode keyNode : keysNode) {
                String keyId = text(keyNode, "kid");
                if (!RSA_KEY_TYPE.equals(text(keyNode, "kty"))
                        || !RSA_SIGNATURE_ALGORITHM.equals(optionalText(keyNode, "alg", RSA_SIGNATURE_ALGORITHM))
                        || !"sig".equals(optionalText(keyNode, "use", "sig"))) {
                    throw new GatewaySecurityException("invalid agent context JWKS key");
                }
                byte[] modulus = Base64.getUrlDecoder().decode(text(keyNode, "n"));
                byte[] exponent = Base64.getUrlDecoder().decode(text(keyNode, "e"));
                RSAPublicKeySpec spec = new RSAPublicKeySpec(
                        new BigInteger(1, modulus), new BigInteger(1, exponent)
                );
                PublicKey publicKey = KeyFactory.getInstance("RSA").generatePublic(spec);
                keys.put(keyId, publicKey);
            }
            return keys;
        } catch (GatewaySecurityException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new GatewaySecurityException("invalid agent context JWKS");
        }
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.isTextual() || value.asText().trim().isEmpty()) {
            throw new GatewaySecurityException("invalid agent context JWKS field: " + field);
        }
        return value.asText();
    }

    private static String optionalText(JsonNode node, String field, String defaultValue) {
        JsonNode value = node == null ? null : node.get(field);
        return value == null ? defaultValue : text(node, field);
    }

    private static final class CacheSnapshot {
        private final Map<String, PublicKey> keys;
        private final Instant expiresAt;

        private CacheSnapshot(Map<String, PublicKey> keys, Instant expiresAt) {
            this.keys = keys;
            this.expiresAt = expiresAt;
        }
    }
}

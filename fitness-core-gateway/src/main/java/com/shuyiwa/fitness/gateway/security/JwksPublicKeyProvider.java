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
    private static final int MIN_RSA_KEY_BITS = 2048;
    private static final int MAX_KEYS = 50;
    private static final long UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS = 30L;
    private final ObjectMapper objectMapper;
    private final String jwksUrl;
    private final long cacheSeconds;
    private final RestTemplate restTemplate;
    private final Clock clock;
    private volatile CacheSnapshot cache;

    public JwksPublicKeyProvider(ObjectMapper objectMapper, GatewayProperties properties) {
        this(
                objectMapper,
                properties.getContextVerificationJwksUrl(),
                properties.getContextVerificationJwksCacheSeconds(),
                properties.getContextVerificationJwksTimeoutMilliseconds()
        );
    }

    /** 供确认凭证等其他签名域复用同一 JWKS 实现，但使用独立的配置和缓存实例。 */
    public JwksPublicKeyProvider(
            ObjectMapper objectMapper,
            String jwksUrl,
            long cacheSeconds,
            long timeoutMilliseconds
    ) {
        this(
                objectMapper,
                jwksUrl,
                cacheSeconds,
                new RestTemplateBuilder()
                        .setConnectTimeout(Duration.ofMillis(timeoutMilliseconds))
                        .setReadTimeout(Duration.ofMillis(timeoutMilliseconds))
                        .build(),
                Clock.systemUTC()
        );
    }

    private JwksPublicKeyProvider(
            ObjectMapper objectMapper,
            String jwksUrl,
            long cacheSeconds,
            RestTemplate restTemplate,
            Clock clock
    ) {
        this.objectMapper = objectMapper;
        this.jwksUrl = jwksUrl;
        this.cacheSeconds = cacheSeconds;
        this.restTemplate = restTemplate;
        this.clock = clock;
    }

    /*
     * 保留一个兼容测试构造函数，避免测试需要启动真实 HTTP 服务。
     */
    JwksPublicKeyProvider(
            ObjectMapper objectMapper,
            GatewayProperties properties,
            RestTemplate restTemplate,
            Clock clock
    ) {
        this(
                objectMapper,
                properties.getContextVerificationJwksUrl(),
                properties.getContextVerificationJwksCacheSeconds(),
                restTemplate,
                clock
        );
    }

    @Override
    public PublicKey getPublicKey(String keyId) {
        if (jwksUrl == null || jwksUrl.trim().isEmpty()) {
            return null;
        }
        CacheSnapshot current = cache;
        Instant now = clock.instant();
        boolean refreshed = false;
        if (current == null || !now.isBefore(current.expiresAt)) {
            synchronized (this) {
                current = cache;
                if (current == null || !now.isBefore(current.expiresAt)) {
                    current = refresh(jwksUrl, now);
                    cache = current;
                    refreshed = true;
                }
            }
        }
        PublicKey publicKey = current.keys.get(keyId);
        if (publicKey != null || refreshed) {
            return publicKey;
        }

        // 认证服务轮换到新 kid 后，新 Token 可能早于缓存 TTL 出现。未知 kid 只触发
        // 一次受控刷新，并设置冷却窗口，避免攻击者伪造大量 kid 打爆认证服务。
        synchronized (this) {
            current = cache;
            if (current == null) {
                return null;
            }
            publicKey = current.keys.get(keyId);
            if (publicKey != null) {
                return publicKey;
            }
            if (lastUnknownKidRefreshAt != null
                    && now.isBefore(lastUnknownKidRefreshAt.plusSeconds(UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS))) {
                return null;
            }
            lastUnknownKidRefreshAt = now;
            current = refresh(jwksUrl, now);
            cache = current;
            return current.keys.get(keyId);
        }
    }

    private Instant lastUnknownKidRefreshAt;

    private CacheSnapshot refresh(String jwksUrl, Instant now) {
        try {
            String document = fetchJwksDocument(jwksUrl);
            Map<String, PublicKey> keys = parse(document);
            return new CacheSnapshot(
                    Collections.unmodifiableMap(keys),
                    now.plusSeconds(cacheSeconds)
            );
        } catch (GatewaySecurityException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new GatewaySecurityException("AgentContext 的 JWKS 不可用");
        }
    }

    /** 抽出的网络边界，生产使用 RestTemplate，单元测试可注入内存文档而不监听端口。 */
    protected String fetchJwksDocument(String jwksUrl) {
        return restTemplate.getForObject(jwksUrl, String.class);
    }

    private Map<String, PublicKey> parse(String document) {
        if (document == null || document.isEmpty()) {
            throw new GatewaySecurityException("AgentContext 的 JWKS 无效");
        }
        try {
            JsonNode root = objectMapper.readTree(document);
            JsonNode keysNode = root == null ? null : root.get("keys");
            if (keysNode == null || !keysNode.isArray()
                    || keysNode.size() == 0 || keysNode.size() > MAX_KEYS) {
                throw new GatewaySecurityException("AgentContext 的 JWKS 无效");
            }
            Map<String, PublicKey> keys = new HashMap<>();
            for (JsonNode keyNode : keysNode) {
                String keyId = text(keyNode, "kid");
                if (!RSA_KEY_TYPE.equals(text(keyNode, "kty"))
                        || !RSA_SIGNATURE_ALGORITHM.equals(optionalText(keyNode, "alg", RSA_SIGNATURE_ALGORITHM))
                        || !"sig".equals(optionalText(keyNode, "use", "sig"))) {
                    throw new GatewaySecurityException("AgentContext 的 JWKS 密钥无效");
                }
                byte[] modulus = Base64.getUrlDecoder().decode(text(keyNode, "n"));
                byte[] exponent = Base64.getUrlDecoder().decode(text(keyNode, "e"));
                BigInteger modulusValue = new BigInteger(1, modulus);
                BigInteger exponentValue = new BigInteger(1, exponent);
                // 与 Agent 侧保持一致：拒绝弱密钥、偶数指数和重复 kid，避免两端
                // 对同一份认证服务 JWKS 得出不同的安全结论。
                if (keys.containsKey(keyId)
                        || modulusValue.bitLength() < MIN_RSA_KEY_BITS
                        || exponentValue.compareTo(BigInteger.valueOf(3L)) < 0
                        || !exponentValue.testBit(0)) {
            throw new GatewaySecurityException("AgentContext 的 RSA 密钥无效");
                }
                RSAPublicKeySpec spec = new RSAPublicKeySpec(
                        modulusValue, exponentValue
                );
                PublicKey publicKey = KeyFactory.getInstance("RSA").generatePublic(spec);
                keys.put(keyId, publicKey);
            }
            return keys;
        } catch (GatewaySecurityException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new GatewaySecurityException("AgentContext 的 JWKS 无效");
        }
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.isTextual() || value.asText().trim().isEmpty()) {
            throw new GatewaySecurityException("AgentContext 的 JWKS 字段无效：" + field);
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

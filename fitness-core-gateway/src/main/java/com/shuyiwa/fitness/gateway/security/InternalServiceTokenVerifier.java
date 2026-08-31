package com.shuyiwa.fitness.gateway.security;

import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * 校验 Agent 服务到 Gateway 的服务间 Token。
 *
 * <p>使用定长 HMAC 结果比较的方式避免普通字符串比较带来的时序差异。Token 本身
 * 只证明调用方是受信任服务，不包含用户身份；用户身份必须来自独立的 AgentContext。</p>
 */
@Component
public class InternalServiceTokenVerifier {

    private final GatewayProperties properties;

    public InternalServiceTokenVerifier(GatewayProperties properties) {
        this.properties = properties;
    }

    public void verify(String presentedToken) {
        String configuredToken = properties.getInternalServiceToken();
        if (configuredToken == null || configuredToken.isEmpty()
                || presentedToken == null || presentedToken.isEmpty()
                || !MessageDigest.isEqual(
                configuredToken.getBytes(StandardCharsets.UTF_8),
                presentedToken.getBytes(StandardCharsets.UTF_8))) {
            throw new GatewaySecurityException("内部服务凭证无效");
        }
    }
}

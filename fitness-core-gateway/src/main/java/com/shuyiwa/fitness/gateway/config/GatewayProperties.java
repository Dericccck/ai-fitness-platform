package com.shuyiwa.fitness.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Gateway 的安全配置。
 *
 * <p>内部服务 Token 和 AgentContext 签名密钥是两层不同的边界：前者确认请求来自
 * 已登记的 Agent 服务，后者确认上下文由受信任的认证服务签发。两者缺一不可，不能
 * 只依赖来源 IP 或客户端传入的用户 ID。</p>
 */
@ConfigurationProperties(prefix = "gateway.security")
public class GatewayProperties {

    private String internalServiceToken = "";
    private String contextSigningSecret = "";
    private long maxContextTtlSeconds = 300L;

    public String getInternalServiceToken() {
        return internalServiceToken;
    }

    public void setInternalServiceToken(String internalServiceToken) {
        this.internalServiceToken = internalServiceToken;
    }

    public String getContextSigningSecret() {
        return contextSigningSecret;
    }

    public void setContextSigningSecret(String contextSigningSecret) {
        this.contextSigningSecret = contextSigningSecret;
    }

    public long getMaxContextTtlSeconds() {
        return maxContextTtlSeconds;
    }

    public void setMaxContextTtlSeconds(long maxContextTtlSeconds) {
        this.maxContextTtlSeconds = maxContextTtlSeconds;
    }
}

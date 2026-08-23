package com.shuyiwa.fitness.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.HashMap;
import java.util.Map;

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
    /** 当前签名契约使用的算法名称；v1 只允许 HS256，避免算法降级或混淆。 */
    private String contextSigningAlgorithm = "HS256";
    /** 当前主密钥的标识；轮换时新 Token 使用这个 ID。 */
    private String contextSigningKeyId = "legacy";
    /** 轮换期间保留的旧密钥，key 为 Token 中的 kid，value 由 Secret Manager 注入。 */
    private Map<String, String> contextSigningKeyRing = new HashMap<>();
    private long maxContextTtlSeconds = 300L;
    private String confirmationSigningSecret = "";

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

    public String getContextSigningAlgorithm() {
        return contextSigningAlgorithm;
    }

    public void setContextSigningAlgorithm(String contextSigningAlgorithm) {
        this.contextSigningAlgorithm = contextSigningAlgorithm;
    }

    public String getContextSigningKeyId() {
        return contextSigningKeyId;
    }

    public void setContextSigningKeyId(String contextSigningKeyId) {
        this.contextSigningKeyId = contextSigningKeyId;
    }

    public Map<String, String> getContextSigningKeyRing() {
        return contextSigningKeyRing;
    }

    public void setContextSigningKeyRing(Map<String, String> contextSigningKeyRing) {
        this.contextSigningKeyRing = contextSigningKeyRing == null
                ? new HashMap<>()
                : new HashMap<>(contextSigningKeyRing);
    }

    public long getMaxContextTtlSeconds() {
        return maxContextTtlSeconds;
    }

    public void setMaxContextTtlSeconds(long maxContextTtlSeconds) {
        this.maxContextTtlSeconds = maxContextTtlSeconds;
    }

    public String getConfirmationSigningSecret() {
        return confirmationSigningSecret;
    }

    public void setConfirmationSigningSecret(String confirmationSigningSecret) {
        this.confirmationSigningSecret = confirmationSigningSecret;
    }
}

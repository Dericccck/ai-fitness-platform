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
    /** RS256 验签使用的 PEM 公钥环；私钥永远不进入 Gateway。 */
    private Map<String, String> contextVerificationPublicKeyRing = new HashMap<>();
    /** 可选认证服务 JWKS 地址；配置后用于补充或替代静态公钥环。 */
    private String contextVerificationJwksUrl = "";
    private long contextVerificationJwksCacheSeconds = 300L;
    private long contextVerificationJwksTimeoutMilliseconds = 2000L;
    private long maxContextTtlSeconds = 300L;
    private String confirmationSigningSecret = "";
    /** 确认凭证允许的签名算法；本地默认保持 HMAC v1。 */
    private String confirmationSigningAlgorithm = "HS256";
    private String confirmationSigningKeyId = "legacy";
    private Map<String, String> confirmationSigningKeyRing = new HashMap<>();
    /** RS256 确认凭证只读取公钥，私钥由 Agent 服务或认证服务持有。 */
    private Map<String, String> confirmationVerificationPublicKeyRing = new HashMap<>();
    private String confirmationVerificationJwksUrl = "";
    private long confirmationVerificationJwksCacheSeconds = 300L;
    private long confirmationVerificationJwksTimeoutMilliseconds = 2000L;

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

    public Map<String, String> getContextVerificationPublicKeyRing() {
        return contextVerificationPublicKeyRing;
    }

    public void setContextVerificationPublicKeyRing(Map<String, String> contextVerificationPublicKeyRing) {
        this.contextVerificationPublicKeyRing = contextVerificationPublicKeyRing == null
                ? new HashMap<>()
                : new HashMap<>(contextVerificationPublicKeyRing);
    }

    public String getContextVerificationJwksUrl() {
        return contextVerificationJwksUrl;
    }

    public void setContextVerificationJwksUrl(String contextVerificationJwksUrl) {
        this.contextVerificationJwksUrl = contextVerificationJwksUrl;
    }

    public long getContextVerificationJwksCacheSeconds() {
        return contextVerificationJwksCacheSeconds;
    }

    public void setContextVerificationJwksCacheSeconds(long contextVerificationJwksCacheSeconds) {
        this.contextVerificationJwksCacheSeconds = contextVerificationJwksCacheSeconds;
    }

    public long getContextVerificationJwksTimeoutMilliseconds() {
        return contextVerificationJwksTimeoutMilliseconds;
    }

    public void setContextVerificationJwksTimeoutMilliseconds(long contextVerificationJwksTimeoutMilliseconds) {
        this.contextVerificationJwksTimeoutMilliseconds = contextVerificationJwksTimeoutMilliseconds;
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

    public String getConfirmationSigningAlgorithm() {
        return confirmationSigningAlgorithm;
    }

    public void setConfirmationSigningAlgorithm(String confirmationSigningAlgorithm) {
        this.confirmationSigningAlgorithm = confirmationSigningAlgorithm;
    }

    public String getConfirmationSigningKeyId() {
        return confirmationSigningKeyId;
    }

    public void setConfirmationSigningKeyId(String confirmationSigningKeyId) {
        this.confirmationSigningKeyId = confirmationSigningKeyId;
    }

    public Map<String, String> getConfirmationSigningKeyRing() {
        return confirmationSigningKeyRing;
    }

    public void setConfirmationSigningKeyRing(Map<String, String> confirmationSigningKeyRing) {
        this.confirmationSigningKeyRing = confirmationSigningKeyRing == null
                ? new HashMap<>()
                : new HashMap<>(confirmationSigningKeyRing);
    }

    public Map<String, String> getConfirmationVerificationPublicKeyRing() {
        return confirmationVerificationPublicKeyRing;
    }

    public void setConfirmationVerificationPublicKeyRing(
            Map<String, String> confirmationVerificationPublicKeyRing
    ) {
        this.confirmationVerificationPublicKeyRing = confirmationVerificationPublicKeyRing == null
                ? new HashMap<>()
                : new HashMap<>(confirmationVerificationPublicKeyRing);
    }

    public String getConfirmationVerificationJwksUrl() {
        return confirmationVerificationJwksUrl;
    }

    public void setConfirmationVerificationJwksUrl(String confirmationVerificationJwksUrl) {
        this.confirmationVerificationJwksUrl = confirmationVerificationJwksUrl;
    }

    public long getConfirmationVerificationJwksCacheSeconds() {
        return confirmationVerificationJwksCacheSeconds;
    }

    public void setConfirmationVerificationJwksCacheSeconds(long confirmationVerificationJwksCacheSeconds) {
        this.confirmationVerificationJwksCacheSeconds = confirmationVerificationJwksCacheSeconds;
    }

    public long getConfirmationVerificationJwksTimeoutMilliseconds() {
        return confirmationVerificationJwksTimeoutMilliseconds;
    }

    public void setConfirmationVerificationJwksTimeoutMilliseconds(
            long confirmationVerificationJwksTimeoutMilliseconds
    ) {
        this.confirmationVerificationJwksTimeoutMilliseconds = confirmationVerificationJwksTimeoutMilliseconds;
    }
}

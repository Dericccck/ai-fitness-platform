package com.shuyiwa.fitness.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** Gateway 调用客服事实服务的内部配置，不能复用面向 Agent 的 Token。 */
@ConfigurationProperties(prefix = "gateway.customer-service")
public class CustomerServiceProperties {

    private String baseUrl = "http://127.0.0.1:8084";
    private String internalServiceToken = "";
    private int timeoutMilliseconds = 5000;

    public String getBaseUrl() { return baseUrl; }
    public void setBaseUrl(String baseUrl) { this.baseUrl = baseUrl; }
    public String getInternalServiceToken() { return internalServiceToken; }
    public void setInternalServiceToken(String internalServiceToken) { this.internalServiceToken = internalServiceToken; }
    public int getTimeoutMilliseconds() { return timeoutMilliseconds; }
    public void setTimeoutMilliseconds(int timeoutMilliseconds) { this.timeoutMilliseconds = timeoutMilliseconds; }
}

package com.shuyiwa.fitness.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** Gateway 调用预约写服务的内部配置，不能复用面向 Agent 的认证 Token。 */
@ConfigurationProperties(prefix = "gateway.booking-service")
public class BookingServiceProperties {
    private String baseUrl = "http://127.0.0.1:8083";
    private String internalServiceToken = "";
    private int timeoutMilliseconds = 5000;

    public String getBaseUrl() { return baseUrl; }
    public void setBaseUrl(String baseUrl) { this.baseUrl = baseUrl; }
    public String getInternalServiceToken() { return internalServiceToken; }
    public void setInternalServiceToken(String internalServiceToken) { this.internalServiceToken = internalServiceToken; }
    public int getTimeoutMilliseconds() { return timeoutMilliseconds; }
    public void setTimeoutMilliseconds(int timeoutMilliseconds) { this.timeoutMilliseconds = timeoutMilliseconds; }
}

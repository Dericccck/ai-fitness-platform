package com.shuyiwa.fitness.customer.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 客服服务的内部调用配置。
 *
 * <p>空 Token 默认拒绝所有业务请求，保证服务不会因为本地漏配而意外暴露。</p>
 */
@ConfigurationProperties(prefix = "customer-service")
public class CustomerServiceProperties {

    private String internalServiceToken = "";
    private boolean schemaInitEnabled = true;

    public String getInternalServiceToken() {
        return internalServiceToken;
    }

    public void setInternalServiceToken(String internalServiceToken) {
        this.internalServiceToken = internalServiceToken;
    }

    public boolean isSchemaInitEnabled() {
        return schemaInitEnabled;
    }

    public void setSchemaInitEnabled(boolean schemaInitEnabled) {
        this.schemaInitEnabled = schemaInitEnabled;
    }
}

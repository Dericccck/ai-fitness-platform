package com.shuyiwa.fitness.booking.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 预约写服务配置。
 *
 * <p>写服务必须使用独立业务账号；空 Token 会让所有内部请求失败，避免错误配置时
 * 服务被当成公开写接口。</p>
 */
@ConfigurationProperties(prefix = "booking")
public class BookingProperties {

    private String internalServiceToken = "";
    private boolean schemaInitEnabled = true;

    public String getInternalServiceToken() { return internalServiceToken; }
    public void setInternalServiceToken(String internalServiceToken) { this.internalServiceToken = internalServiceToken; }
    public boolean isSchemaInitEnabled() { return schemaInitEnabled; }
    public void setSchemaInitEnabled(boolean schemaInitEnabled) { this.schemaInitEnabled = schemaInitEnabled; }
}

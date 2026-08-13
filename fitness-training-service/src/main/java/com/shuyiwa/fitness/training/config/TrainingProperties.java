package com.shuyiwa.fitness.training.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 训练服务的内部调用配置。空 Token 会让服务拒绝所有业务请求，默认 fail-closed。 */
@ConfigurationProperties(prefix = "training")
public class TrainingProperties {

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

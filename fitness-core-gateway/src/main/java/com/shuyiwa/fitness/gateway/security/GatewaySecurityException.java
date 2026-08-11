package com.shuyiwa.fitness.gateway.security;

/** 认证失败，客户端需要重新获取内部凭证或 AgentContext。 */
public class GatewaySecurityException extends RuntimeException {

    public GatewaySecurityException(String message) {
        super(message);
    }
}

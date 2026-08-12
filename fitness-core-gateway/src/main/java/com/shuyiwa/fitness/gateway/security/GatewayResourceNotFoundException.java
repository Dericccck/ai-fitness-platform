package com.shuyiwa.fitness.gateway.security;

/** 请求的健身核心资源不存在；与权限拒绝保持不同错误语义。 */
public class GatewayResourceNotFoundException extends RuntimeException {

    public GatewayResourceNotFoundException(String message) {
        super(message);
    }
}

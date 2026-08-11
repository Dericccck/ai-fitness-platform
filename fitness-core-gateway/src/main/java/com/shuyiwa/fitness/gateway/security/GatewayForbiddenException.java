package com.shuyiwa.fitness.gateway.security;

/** 认证通过但没有访问指定健身资源的权限。 */
public class GatewayForbiddenException extends RuntimeException {

    public GatewayForbiddenException(String message) {
        super(message);
    }
}

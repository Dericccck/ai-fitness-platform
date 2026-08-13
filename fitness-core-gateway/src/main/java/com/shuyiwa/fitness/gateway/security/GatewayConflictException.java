package com.shuyiwa.fitness.gateway.security;

public class GatewayConflictException extends RuntimeException {
    public GatewayConflictException(String message) {
        super(message);
    }
}

package com.shuyiwa.fitness.customer.security;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ResponseStatus;

/** 客服服务拒绝内部认证或主体声明。 */
@ResponseStatus(HttpStatus.FORBIDDEN)
public class CustomerServiceSecurityException extends RuntimeException {

    public CustomerServiceSecurityException(String message) {
        super(message);
    }
}

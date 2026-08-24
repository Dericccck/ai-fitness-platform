package com.shuyiwa.fitness.customer.api;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ResponseStatus;

/** 同一请求 ID 对应了不同业务内容，或确认 JTI 被重复使用。 */
@ResponseStatus(HttpStatus.CONFLICT)
public class CustomerServiceConflictException extends RuntimeException {
    public CustomerServiceConflictException(String message) {
        super(message);
    }
}

package com.shuyiwa.fitness.customer.api;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ResponseStatus;

@ResponseStatus(HttpStatus.NOT_FOUND)
public class CustomerServiceTicketNotFoundException extends RuntimeException {
    public CustomerServiceTicketNotFoundException(String message) {
        super(message);
    }
}

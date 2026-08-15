package com.shuyiwa.fitness.booking.api;

import org.springframework.http.HttpStatus;

/** 预约业务异常，统一映射成稳定 HTTP 状态而不是泄露 SQL 细节。 */
public class BookingApiException extends RuntimeException {
    private final HttpStatus status;

    public BookingApiException(HttpStatus status, String message) {
        super(message);
        this.status = status;
    }

    public HttpStatus getStatus() { return status; }
}

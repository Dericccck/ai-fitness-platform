package com.shuyiwa.fitness.booking.api;

import com.shuyiwa.fitness.booking.security.BookingSecurityException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.Collections;
import java.util.Map;

/** 预约服务错误边界，客户端只接收可处理的业务语义。 */
@RestControllerAdvice
public class BookingExceptionHandler {
    @ExceptionHandler(BookingApiException.class)
    public ResponseEntity<Map<String, String>> business(BookingApiException exception) {
        return ResponseEntity.status(exception.getStatus())
                .body(Collections.singletonMap("message", exception.getMessage()));
    }

    @ExceptionHandler(BookingSecurityException.class)
    public ResponseEntity<Map<String, String>> security(BookingSecurityException exception) {
        return ResponseEntity.status(401).body(Collections.singletonMap("message", exception.getMessage()));
    }
}

package com.shuyiwa.fitness.booking.security;

/** 内部服务认证或主体声明缺失时的 fail-closed 异常。 */
public class BookingSecurityException extends RuntimeException {
    public BookingSecurityException(String message) { super(message); }
}

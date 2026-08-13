package com.shuyiwa.fitness.gateway.api;

import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import com.shuyiwa.fitness.gateway.security.GatewayResourceNotFoundException;
import com.shuyiwa.fitness.gateway.security.GatewaySecurityException;
import com.shuyiwa.fitness.gateway.security.GatewayConflictException;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** 统一返回不泄露内部 SQL、用户存在性细节的错误响应。 */
@RestControllerAdvice
public class GatewayExceptionHandler {

    @ExceptionHandler(GatewaySecurityException.class)
    @ResponseStatus(HttpStatus.UNAUTHORIZED)
    public ErrorView unauthorized() {
        return new ErrorView("UNAUTHORIZED", "authentication required");
    }

    @ExceptionHandler(GatewayForbiddenException.class)
    @ResponseStatus(HttpStatus.FORBIDDEN)
    public ErrorView forbidden() {
        return new ErrorView("FORBIDDEN", "resource is outside the authorized scope");
    }

    @ExceptionHandler(GatewayResourceNotFoundException.class)
    @ResponseStatus(HttpStatus.NOT_FOUND)
    public ErrorView notFound() {
        return new ErrorView("NOT_FOUND", "fitness resource was not found");
    }

    @ExceptionHandler(GatewayConflictException.class)
    @ResponseStatus(HttpStatus.CONFLICT)
    public ErrorView conflict() {
        return new ErrorView("CONFLICT", "fitness resource was changed by another request");
    }

    @ExceptionHandler(IllegalArgumentException.class)
    @ResponseStatus(HttpStatus.BAD_REQUEST)
    public ErrorView badRequest(IllegalArgumentException exception) {
        return new ErrorView("INVALID_ARGUMENT", exception.getMessage());
    }

    public static final class ErrorView {
        private final String code;
        private final String message;

        public ErrorView(String code, String message) {
            this.code = code;
            this.message = message;
        }

        public String getCode() { return code; }
        public String getMessage() { return message; }
    }
}

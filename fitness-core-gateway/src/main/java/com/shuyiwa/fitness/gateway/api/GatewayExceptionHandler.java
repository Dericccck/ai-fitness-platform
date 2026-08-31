package com.shuyiwa.fitness.gateway.api;

import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import com.shuyiwa.fitness.gateway.security.GatewayResourceNotFoundException;
import com.shuyiwa.fitness.gateway.security.GatewaySecurityException;
import com.shuyiwa.fitness.gateway.security.GatewayConflictException;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.servlet.http.HttpServletRequest;

/** 统一返回不泄露内部 SQL、用户存在性细节的错误响应。 */
@RestControllerAdvice
public class GatewayExceptionHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(GatewayExceptionHandler.class);

    @ExceptionHandler(GatewaySecurityException.class)
    @ResponseStatus(HttpStatus.UNAUTHORIZED)
    public ErrorView unauthorized(GatewaySecurityException exception, HttpServletRequest request) {
        // 对客户端继续返回统一 401，避免泄露鉴权细节；服务端保留脱敏原因和 request_id，
        // 便于按链路定位是服务间 Token、AgentContext 还是确认凭证校验失败。
        LOGGER.warn("gateway_authentication_rejected path={} requestId={} reason={}",
                request.getRequestURI(), request.getHeader("X-Request-ID"), exception.getMessage());
        return new ErrorView("UNAUTHORIZED", "需要身份验证");
    }

    @ExceptionHandler(GatewayForbiddenException.class)
    @ResponseStatus(HttpStatus.FORBIDDEN)
    public ErrorView forbidden() {
        return new ErrorView("FORBIDDEN", "资源不在授权范围内");
    }

    @ExceptionHandler(GatewayResourceNotFoundException.class)
    @ResponseStatus(HttpStatus.NOT_FOUND)
    public ErrorView notFound() {
        return new ErrorView("NOT_FOUND", "健身资源不存在");
    }

    @ExceptionHandler(GatewayConflictException.class)
    @ResponseStatus(HttpStatus.CONFLICT)
    public ErrorView conflict() {
        return new ErrorView("CONFLICT", "健身资源已被其他请求修改");
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

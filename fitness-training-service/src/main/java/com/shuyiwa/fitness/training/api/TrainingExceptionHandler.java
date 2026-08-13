package com.shuyiwa.fitness.training.api;

import com.shuyiwa.fitness.training.security.TrainingSecurityException;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.ResponseBody;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.Map;

/** 统一返回稳定错误码，不把 SQL、堆栈和内部表名泄露给 Agent 或前端。 */
@RestControllerAdvice
public class TrainingExceptionHandler {

    @ExceptionHandler(TrainingSecurityException.class)
    @ResponseBody
    public Map<String, String> security(TrainingSecurityException exception,
                                        javax.servlet.http.HttpServletResponse response) {
        response.setStatus(HttpStatus.UNAUTHORIZED.value());
        return error("UNAUTHORIZED", exception.getMessage());
    }

    @ExceptionHandler(TrainingApiException.class)
    @ResponseBody
    public Map<String, String> business(TrainingApiException exception,
                                        javax.servlet.http.HttpServletResponse response) {
        response.setStatus(exception.getStatus().value());
        String code = exception.getStatus() == HttpStatus.CONFLICT ? "CONFLICT"
                : exception.getStatus() == HttpStatus.FORBIDDEN ? "FORBIDDEN"
                : exception.getStatus() == HttpStatus.NOT_FOUND ? "NOT_FOUND" : "INVALID_ARGUMENT";
        return error(code, exception.getMessage());
    }

    @ExceptionHandler(IllegalArgumentException.class)
    @ResponseBody
    public Map<String, String> argument(IllegalArgumentException exception,
                                        javax.servlet.http.HttpServletResponse response) {
        response.setStatus(HttpStatus.BAD_REQUEST.value());
        return error("INVALID_ARGUMENT", exception.getMessage());
    }

    private Map<String, String> error(String code, String message) {
        Map<String, String> result = new java.util.HashMap<>();
        result.put("code", code);
        result.put("message", message);
        return result;
    }
}

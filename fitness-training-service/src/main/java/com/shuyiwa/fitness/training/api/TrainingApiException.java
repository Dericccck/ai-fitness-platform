package com.shuyiwa.fitness.training.api;

import org.springframework.http.HttpStatus;

public class TrainingApiException extends RuntimeException {
    private final HttpStatus status;

    public TrainingApiException(HttpStatus status, String message) {
        super(message);
        this.status = status;
    }

    public HttpStatus getStatus() {
        return status;
    }
}

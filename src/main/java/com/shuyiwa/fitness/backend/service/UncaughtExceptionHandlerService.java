package com.shuyiwa.fitness.backend.service;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import javax.annotation.PostConstruct;

@Service
public class UncaughtExceptionHandlerService {
    private static final Log logger = LogFactory.getLog(UncaughtExceptionHandlerService.class);
    @Autowired
    WarnService warnService;

    @PostConstruct
    void init() {
        Thread.setDefaultUncaughtExceptionHandler((t, e) -> {
            String message = "uncaughtException:" + t;
            logger.warn(message, e);
            warnService.warn(message, e);
        });
    }
}

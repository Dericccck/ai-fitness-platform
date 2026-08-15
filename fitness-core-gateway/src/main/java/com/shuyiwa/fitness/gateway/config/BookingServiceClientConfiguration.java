package com.shuyiwa.fitness.gateway.config;

import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestTemplate;

import java.time.Duration;

/** 为 Gateway 到预约写服务创建独立 HTTP 客户端。 */
@Configuration
public class BookingServiceClientConfiguration {
    @Bean
    public RestTemplate bookingServiceRestTemplate(RestTemplateBuilder builder,
                                                   BookingServiceProperties properties) {
        return builder
                .setConnectTimeout(Duration.ofMillis(properties.getTimeoutMilliseconds()))
                .setReadTimeout(Duration.ofMillis(properties.getTimeoutMilliseconds()))
                .build();
    }
}

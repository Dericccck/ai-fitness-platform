package com.shuyiwa.fitness.gateway.config;

import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestTemplate;

import java.time.Duration;

/** 为 Gateway 到训练服务的内部调用创建独立 HTTP 客户端，避免复用外部请求连接配置。 */
@Configuration
public class TrainingServiceClientConfiguration {

    @Bean
    public RestTemplate trainingServiceRestTemplate(RestTemplateBuilder builder,
                                                    TrainingServiceProperties properties) {
        return builder
                .setConnectTimeout(Duration.ofMillis(properties.getTimeoutMilliseconds()))
                .setReadTimeout(Duration.ofMillis(properties.getTimeoutMilliseconds()))
                .build();
    }
}

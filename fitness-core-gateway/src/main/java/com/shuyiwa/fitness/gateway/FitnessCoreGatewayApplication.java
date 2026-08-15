package com.shuyiwa.fitness.gateway;

import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import com.shuyiwa.fitness.gateway.config.TrainingServiceProperties;
import com.shuyiwa.fitness.gateway.config.BookingServiceProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

/**
 * 健身核心 Tool Gateway 的独立启动入口。
 *
 * <p>这个服务故意不依赖旧 Java 根项目的 Spring 扫描路径和实体类，避免赛事、作品、
 * 活动等历史代码通过组件扫描重新进入 Agent 的业务边界。它只通过受控的只读 SQL
 * 访问健身核心数据，并在后续阶段增加经过审计的写工具。</p>
 */
@SpringBootApplication
@EnableConfigurationProperties({GatewayProperties.class, TrainingServiceProperties.class, BookingServiceProperties.class})
public class FitnessCoreGatewayApplication {

    public static void main(String[] args) {
        SpringApplication.run(FitnessCoreGatewayApplication.class, args);
    }
}

package com.shuyiwa.fitness.customer;

import com.shuyiwa.fitness.customer.config.CustomerServiceProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

/** 客服工单业务服务启动入口。客服事实与 Agent Runtime、旧根项目保持进程隔离。 */
@SpringBootApplication
@EnableConfigurationProperties(CustomerServiceProperties.class)
public class FitnessCustomerServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(FitnessCustomerServiceApplication.class, args);
    }
}

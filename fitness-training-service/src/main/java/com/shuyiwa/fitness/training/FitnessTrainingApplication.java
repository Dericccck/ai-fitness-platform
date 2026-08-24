package com.shuyiwa.fitness.training;

import com.shuyiwa.fitness.training.config.TrainingProperties;
import com.shuyiwa.fitness.training.config.TrainingOutboxProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

/** 训练业务服务启动入口。它和只读 Tool Gateway 分离，拥有训练业务表的写权限。 */
@SpringBootApplication
@EnableConfigurationProperties({TrainingProperties.class, TrainingOutboxProperties.class})
@EnableScheduling
public class FitnessTrainingApplication {

    public static void main(String[] args) {
        SpringApplication.run(FitnessTrainingApplication.class, args);
    }
}

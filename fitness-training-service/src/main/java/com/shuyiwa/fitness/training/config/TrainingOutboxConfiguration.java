package com.shuyiwa.fitness.training.config;

import com.shuyiwa.fitness.training.outbox.RabbitTrainingMessagePublisher;
import com.shuyiwa.fitness.training.outbox.TrainingMessagePublisher;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** RabbitMQ 训练事件发布拓扑；与 Booking 共用领域事件 Exchange，但使用独立路由键。 */
@Configuration
@ConditionalOnProperty(prefix = "training.outbox", name = "publisher-enabled", havingValue = "true")
public class TrainingOutboxConfiguration {

    @Bean
    public DirectExchange trainingEventsExchange(TrainingOutboxProperties properties) {
        return new DirectExchange(properties.getExchange(), true, false);
    }

    @Bean
    public TrainingMessagePublisher trainingMessagePublisher(
            RabbitTemplate rabbitTemplate, TrainingOutboxProperties properties) {
        return new RabbitTrainingMessagePublisher(rabbitTemplate, properties);
    }
}

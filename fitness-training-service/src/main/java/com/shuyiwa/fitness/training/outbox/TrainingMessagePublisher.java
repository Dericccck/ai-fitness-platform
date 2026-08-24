package com.shuyiwa.fitness.training.outbox;

/** 训练事件的消息系统适配器；训练业务事务不直接依赖 RabbitMQ API。 */
public interface TrainingMessagePublisher {
    void publish(TrainingOutboxRepository.OutboxEvent event) throws Exception;
}

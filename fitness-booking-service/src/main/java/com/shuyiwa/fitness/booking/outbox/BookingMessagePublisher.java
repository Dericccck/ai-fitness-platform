package com.shuyiwa.fitness.booking.outbox;

/** Outbox 事件的消息系统适配器；业务发布器不直接依赖 RabbitMQ API。 */
public interface BookingMessagePublisher {
    void publish(BookingOutboxRepository.OutboxEvent event) throws Exception;
}

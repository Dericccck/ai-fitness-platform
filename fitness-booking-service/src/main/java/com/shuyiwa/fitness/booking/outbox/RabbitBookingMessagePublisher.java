package com.shuyiwa.fitness.booking.outbox;

import com.shuyiwa.fitness.booking.config.BookingOutboxProperties;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

import java.util.concurrent.TimeUnit;

/**
 * RabbitMQ 发布适配器。
 *
 * <p>RabbitTemplate 调用成功只代表消息已交给客户端发送流程，不能据此更新 Outbox 状态。
 * 这里等待 broker publisher confirm；只有收到 ack 才向上层返回成功，nack、超时或异常都会
 * 进入 Outbox 重试流程。</p>
 */
public class RabbitBookingMessagePublisher implements BookingMessagePublisher {
    private final RabbitTemplate rabbitTemplate;
    private final BookingOutboxProperties properties;

    public RabbitBookingMessagePublisher(RabbitTemplate rabbitTemplate, BookingOutboxProperties properties) {
        this.rabbitTemplate = rabbitTemplate;
        this.properties = properties;
    }

    @Override
    public void publish(BookingOutboxRepository.OutboxEvent event) throws Exception {
        CorrelationData correlation = new CorrelationData(event.eventKey);
        rabbitTemplate.convertAndSend(
                properties.getExchange(), properties.getRoutingKey(), event.payload, correlation);
        CorrelationData.Confirm confirm = correlation.getFuture().get(
                properties.getConfirmTimeoutMs(), TimeUnit.MILLISECONDS);
        if (confirm == null || !confirm.isAck()) {
            String reason = confirm == null ? "empty broker confirmation" : confirm.getReason();
            throw new IllegalStateException("RabbitMQ publisher nack: " + reason);
        }
    }
}

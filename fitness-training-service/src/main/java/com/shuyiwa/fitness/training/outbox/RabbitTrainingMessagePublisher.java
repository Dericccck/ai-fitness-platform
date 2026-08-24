package com.shuyiwa.fitness.training.outbox;

import com.shuyiwa.fitness.training.config.TrainingOutboxProperties;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

import java.util.concurrent.TimeUnit;

/** RabbitMQ 训练事件发布适配器；只有 broker publisher confirm 成功才完成 Outbox。 */
public class RabbitTrainingMessagePublisher implements TrainingMessagePublisher {
    private final RabbitTemplate rabbitTemplate;
    private final TrainingOutboxProperties properties;

    public RabbitTrainingMessagePublisher(RabbitTemplate rabbitTemplate, TrainingOutboxProperties properties) {
        this.rabbitTemplate = rabbitTemplate;
        this.properties = properties;
    }

    @Override
    public void publish(TrainingOutboxRepository.OutboxEvent event) throws Exception {
        CorrelationData correlation = new CorrelationData(event.eventKey);
        rabbitTemplate.convertAndSend(
                properties.getExchange(), routingKey(event.eventType), envelope(event), correlation);
        CorrelationData.Confirm confirm = correlation.getFuture().get(
                properties.getConfirmTimeoutMs(), TimeUnit.MILLISECONDS);
        if (confirm == null || !confirm.isAck()) {
            String reason = confirm == null ? "empty broker confirmation" : confirm.getReason();
            throw new IllegalStateException("RabbitMQ publisher nack: " + reason);
        }
    }

    /** 将训练 Outbox 行包装为 Agent 可校验、可幂等消费的统一事件信封。 */
    private static String envelope(TrainingOutboxRepository.OutboxEvent event) {
        return "{\"eventId\":\"" + escape(event.eventKey)
                + "\",\"source\":\"training\",\"eventType\":\""
                + escape(event.eventType) + "\",\"aggregateId\":\""
                + escape(event.aggregateId) + "\",\"organizationId\":\""
                + escape(event.organizationId) + "\",\"payload\":" + event.payload + "}";
    }

    private static String routingKey(String eventType) {
        switch (eventType) {
            case "TRAINING_PLAN_REVIEW_REQUIRED":
                return "training.plan.review_required";
            case "TRAINING_PLAN_PUBLISHED":
                return "training.plan.published";
            default:
                throw new IllegalArgumentException("unsupported training event type: " + eventType);
        }
    }

    private static String escape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}

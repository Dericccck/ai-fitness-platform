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
                properties.getExchange(), routingKey(event.eventType), envelope(event), correlation);
        CorrelationData.Confirm confirm = correlation.getFuture().get(
                properties.getConfirmTimeoutMs(), TimeUnit.MILLISECONDS);
        if (confirm == null || !confirm.isAck()) {
            String reason = confirm == null ? "消息代理未返回确认信息" : confirm.getReason();
            throw new IllegalStateException("RabbitMQ 发布器未确认消息：" + reason);
        }
    }

    /**
     * 将数据库 Outbox 行包装成跨服务事件信封。
     *
     * <p>原始 payload 只包含预约业务字段，Agent 还需要事件 ID、机构和事件类型完成
     * 幂等、权限范围和通知路由，因此不能直接把原始 JSON 当作消息体发送。</p>
     */
    private static String envelope(BookingOutboxRepository.OutboxEvent event) {
        return "{\"eventId\":\"" + escape(event.eventKey)
                + "\",\"source\":\"booking\",\"eventType\":\""
                + escape(event.eventType) + "\",\"aggregateId\":\""
                + escape(event.aggregateId) + "\",\"organizationId\":\""
                + escape(event.organizationId) + "\",\"payload\":" + event.payload + "}";
    }

    private static String routingKey(String eventType) {
        switch (eventType) {
            case "APPOINTMENT_CREATED":
                return "appointment.created";
            case "APPOINTMENT_RESCHEDULED":
                return "appointment.rescheduled";
            case "APPOINTMENT_CANCELLED":
                return "appointment.cancelled";
            default:
                throw new IllegalArgumentException("不支持的预约事件类型：" + eventType);
        }
    }

    private static String escape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}

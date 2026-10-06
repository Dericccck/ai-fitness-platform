package com.shuyiwa.fitness.booking.config;

import com.shuyiwa.fitness.booking.outbox.BookingMessagePublisher;
import com.shuyiwa.fitness.booking.outbox.RabbitBookingMessagePublisher;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** RabbitMQ 拓扑和消息发布器配置；只有明确开启 Outbox 发布器时才创建。 */
@Configuration
@ConditionalOnProperty(prefix = "booking.outbox", name = "publisher-enabled", havingValue = "true")
public class BookingOutboxConfiguration {

    @Bean
    public DirectExchange bookingEventsExchange(BookingOutboxProperties properties) {
        return new DirectExchange(properties.getExchange(), true, false);
    }

    @Bean
    public BookingMessagePublisher bookingMessagePublisher(RabbitTemplate rabbitTemplate,
                                                            BookingOutboxProperties properties) {
        return new RabbitBookingMessagePublisher(rabbitTemplate, properties);
    }
}

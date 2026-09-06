package com.shuyiwa.fitness.training.outbox;

import com.shuyiwa.fitness.training.config.TrainingOutboxProperties;
import org.junit.Assume;
import org.junit.Test;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitAdmin;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

import java.time.Instant;
import java.util.UUID;

import static org.junit.Assert.assertTrue;

/** 真实 RabbitMQ Publisher ACK/Return 集成测试；仅创建并清理本轮唯一拓扑。 */
public class TrainingRabbitPublisherIntegrationTest {

    @Test
    public void acceptsRoutedAckAndRejectsAckWithMandatoryReturn() throws Exception {
        Assume.assumeTrue("true".equalsIgnoreCase(System.getenv("TRAINING_RABBIT_IT_ENABLED")));
        String suffix = UUID.randomUUID().toString();
        String exchangeName = "fitness.training.publisher.it." + suffix;
        String queueName = exchangeName + ".queue";
        CachingConnectionFactory connectionFactory = new CachingConnectionFactory(
                env("TRAINING_RABBIT_IT_HOST", "127.0.0.1"),
                Integer.parseInt(env("TRAINING_RABBIT_IT_PORT", "5672")));
        connectionFactory.setUsername(env("TRAINING_RABBIT_IT_USERNAME", "fitness_agent"));
        connectionFactory.setPassword(env("TRAINING_RABBIT_IT_PASSWORD", "fitness_agent_secret"));
        connectionFactory.setPublisherConfirms(true);
        connectionFactory.setPublisherReturns(true);
        RabbitAdmin admin = new RabbitAdmin(connectionFactory);
        RabbitTemplate template = new RabbitTemplate(connectionFactory);
        template.setMandatory(true);
        DirectExchange exchange = new DirectExchange(exchangeName, true, false);
        Queue queue = new Queue(queueName, true, false, false);
        try {
            admin.declareExchange(exchange);
            admin.declareQueue(queue);
            admin.declareBinding(BindingBuilder.bind(queue).to(exchange)
                    .with("training.plan.published"));

            publisher(template, exchangeName).publish(event(suffix + ":routed"));

            admin.removeBinding(BindingBuilder.bind(queue).to(exchange)
                    .with("training.plan.published"));
            try {
                publisher(template, exchangeName).publish(event(suffix + ":returned"));
            } catch (IllegalStateException expected) {
                assertTrue(expected.getMessage().contains("publisher return"));
                return;
            }
            throw new AssertionError("ACK 同时收到 mandatory Return 时不能视为发布成功");
        } finally {
            admin.deleteQueue(queueName);
            admin.deleteExchange(exchangeName);
            connectionFactory.destroy();
        }
    }

    private static RabbitTrainingMessagePublisher publisher(
            RabbitTemplate template, String exchangeName) {
        TrainingOutboxProperties properties = new TrainingOutboxProperties();
        properties.setExchange(exchangeName);
        properties.setConfirmTimeoutMs(5000);
        return new RabbitTrainingMessagePublisher(template, properties);
    }

    private static TrainingOutboxRepository.OutboxEvent event(String eventKey) {
        return new TrainingOutboxRepository.OutboxEvent(
                1L, eventKey, "TRAINING_PLAN_PUBLISHED", "plan-it", "org-it", 1,
                "{\"planId\":\"plan-it\",\"studentId\":\"student-it\"}", 0,
                Instant.now());
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        return value == null || value.trim().isEmpty() ? fallback : value.trim();
    }
}

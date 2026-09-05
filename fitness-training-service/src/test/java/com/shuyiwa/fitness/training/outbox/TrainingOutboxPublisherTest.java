package com.shuyiwa.fitness.training.outbox;

import com.shuyiwa.fitness.training.config.TrainingOutboxProperties;
import org.junit.Test;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

import java.util.Collections;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 验证训练计划 Outbox 的成功确认和失败重试分支，不连接真实 RabbitMQ。 */
public class TrainingOutboxPublisherTest {

    @Test
    public void rabbitPublisherAcceptsAckWithoutReturn() throws Exception {
        RabbitTemplate template = mock(RabbitTemplate.class);
        doAnswer(invocation -> {
            CorrelationData correlation = invocation.getArgument(3);
            correlation.getFuture().set(new CorrelationData.Confirm(true, null));
            return null;
        }).when(template).convertAndSend(anyString(), anyString(), (Object) any(),
                any(CorrelationData.class));

        new RabbitTrainingMessagePublisher(template, enabledProperties()).publish(event());
    }

    @Test
    public void rabbitPublisherRejectsAckWhenMessageWasReturned() throws Exception {
        RabbitTemplate template = mock(RabbitTemplate.class);
        doAnswer(invocation -> {
            CorrelationData correlation = invocation.getArgument(3);
            correlation.setReturnedMessage(new Message(new byte[0], new MessageProperties()));
            correlation.getFuture().set(new CorrelationData.Confirm(true, null));
            return null;
        }).when(template).convertAndSend(anyString(), anyString(), (Object) any(),
                any(CorrelationData.class));

        try {
            new RabbitTrainingMessagePublisher(template, enabledProperties()).publish(event());
        } catch (IllegalStateException exception) {
            org.junit.Assert.assertTrue(exception.getMessage().contains("publisher return"));
            return;
        }
        throw new AssertionError("收到 Return 时不能把训练事件视为发布成功");
    }

    @Test
    public void marksEventPublishedOnlyAfterMessagePublisherReturns() throws Exception {
        TrainingOutboxRepository repository = mock(TrainingOutboxRepository.class);
        TrainingMessagePublisher messagePublisher = mock(TrainingMessagePublisher.class);
        TrainingOutboxProperties properties = enabledProperties();
        TrainingOutboxRepository.OutboxEvent event = event();
        when(repository.claimPending(anyString())).thenReturn(Collections.singletonList(event));

        new TrainingOutboxPublisher(repository, messagePublisher, properties).publishPending();

        verify(messagePublisher).publish(event);
        verify(repository).markPublished(eq(1L), anyString());
    }

    @Test
    public void keepsFailedEventInRetryFlow() throws Exception {
        TrainingOutboxRepository repository = mock(TrainingOutboxRepository.class);
        TrainingMessagePublisher messagePublisher = mock(TrainingMessagePublisher.class);
        TrainingOutboxProperties properties = enabledProperties();
        TrainingOutboxRepository.OutboxEvent event = event();
        when(repository.claimPending(anyString())).thenReturn(Collections.singletonList(event));
        when(repository.markFailed(eq(event), anyString(), any(IllegalStateException.class))).thenReturn(true);
        doThrow(new IllegalStateException("RabbitMQ 不可用")).when(messagePublisher).publish(event);

        new TrainingOutboxPublisher(repository, messagePublisher, properties).publishPending();

        verify(repository).markFailed(eq(event), anyString(), any(IllegalStateException.class));
    }

    private static TrainingOutboxProperties enabledProperties() {
        TrainingOutboxProperties properties = new TrainingOutboxProperties();
        properties.setPublisherEnabled(true);
        return properties;
    }

    private static TrainingOutboxRepository.OutboxEvent event() {
        return new TrainingOutboxRepository.OutboxEvent(
                1L, "training-plan-training_plan_published:plan-1:request-1",
                "TRAINING_PLAN_PUBLISHED", "plan-1", "org-1",
                "{\"planId\":\"plan-1\",\"studentId\":\"student-1\"}", 0);
    }
}

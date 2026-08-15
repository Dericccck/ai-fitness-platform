package com.shuyiwa.fitness.booking.outbox;

import com.shuyiwa.fitness.booking.config.BookingOutboxProperties;
import org.junit.Test;

import java.util.Collections;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 验证 Outbox 发布器的成功确认和失败重试分支，不连接真实 RabbitMQ。 */
public class BookingOutboxPublisherTest {

    @Test
    public void marksEventPublishedOnlyAfterMessagePublisherReturns() throws Exception {
        BookingOutboxRepository repository = mock(BookingOutboxRepository.class);
        BookingMessagePublisher messagePublisher = mock(BookingMessagePublisher.class);
        BookingOutboxProperties properties = enabledProperties();
        BookingOutboxRepository.OutboxEvent event = event();
        when(repository.claimPending(anyString())).thenReturn(Collections.singletonList(event));

        new BookingOutboxPublisher(repository, messagePublisher, properties).publishPending();

        verify(messagePublisher).publish(event);
        verify(repository).markPublished(eq(1L), anyString());
    }

    @Test
    public void keepsFailedEventInRetryFlow() throws Exception {
        BookingOutboxRepository repository = mock(BookingOutboxRepository.class);
        BookingMessagePublisher messagePublisher = mock(BookingMessagePublisher.class);
        BookingOutboxProperties properties = enabledProperties();
        BookingOutboxRepository.OutboxEvent event = event();
        when(repository.claimPending(anyString())).thenReturn(Collections.singletonList(event));
        when(repository.markFailed(eq(event), anyString(), any(IllegalStateException.class))).thenReturn(true);
        doThrow(new IllegalStateException("rabbit unavailable")).when(messagePublisher).publish(event);

        new BookingOutboxPublisher(repository, messagePublisher, properties).publishPending();

        verify(repository).markFailed(eq(event), anyString(), any(IllegalStateException.class));
    }

    private static BookingOutboxProperties enabledProperties() {
        BookingOutboxProperties properties = new BookingOutboxProperties();
        properties.setPublisherEnabled(true);
        return properties;
    }

    private static BookingOutboxRepository.OutboxEvent event() {
        return new BookingOutboxRepository.OutboxEvent(
                1L, "appointment-created:appointment-1", "APPOINTMENT_CREATED",
                "appointment-1", "org-1", "{\"appointmentId\":\"appointment-1\"}", 0);
    }
}

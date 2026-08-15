package com.shuyiwa.fitness.booking.outbox;

import com.shuyiwa.fitness.booking.config.BookingOutboxProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 定时领取和发布预约 Outbox。
 *
 * <p>单条事件失败不会阻塞同批其他事件；失败记录保留在数据库中并按指数退避重试，超过
 * 最大次数进入 DEAD，后续可以由运维或人工补偿工具处理。注意：PUBLISHED 只表示 broker
 * 已确认接收，不代表下游业务已经完成。</p>
 */
@Component
@ConditionalOnProperty(prefix = "booking.outbox", name = "publisher-enabled", havingValue = "true")
public class BookingOutboxPublisher {
    private static final Logger log = LoggerFactory.getLogger(BookingOutboxPublisher.class);

    private final BookingOutboxRepository repository;
    private final BookingMessagePublisher messagePublisher;
    private final BookingOutboxProperties properties;
    private final String workerId = BookingOutboxRepository.newWorkerId();

    public BookingOutboxPublisher(BookingOutboxRepository repository,
                                  BookingMessagePublisher messagePublisher,
                                  BookingOutboxProperties properties) {
        this.repository = repository;
        this.messagePublisher = messagePublisher;
        this.properties = properties;
    }

    @Scheduled(fixedDelayString = "${booking.outbox.fixed-delay-ms:1000}")
    public void publishPending() {
        if (!properties.isPublisherEnabled()) return;
        List<BookingOutboxRepository.OutboxEvent> events = repository.claimPending(workerId);
        for (BookingOutboxRepository.OutboxEvent event : events) {
            try {
                messagePublisher.publish(event);
                if (!repository.markPublished(event.id, workerId)) {
                    log.warn("预约 Outbox 已发送但状态更新失败，eventKey={}", event.eventKey);
                }
            } catch (Exception failure) {
                if (!repository.markFailed(event, workerId, failure)) {
                    log.error("预约 Outbox 失败状态更新失败，eventKey={}", event.eventKey, failure);
                } else {
                    log.warn("预约 Outbox 发布失败，将按策略重试，eventKey={}", event.eventKey, failure);
                }
            }
        }
    }
}

package com.shuyiwa.fitness.training.outbox;

import com.shuyiwa.fitness.training.config.TrainingOutboxProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/** 定时领取并发布训练计划事件 Outbox；失败事件有限重试，超过上限进入 DEAD。 */
@Component
@ConditionalOnProperty(prefix = "training.outbox", name = "publisher-enabled", havingValue = "true")
public class TrainingOutboxPublisher {
    private static final Logger log = LoggerFactory.getLogger(TrainingOutboxPublisher.class);

    private final TrainingOutboxRepository repository;
    private final TrainingMessagePublisher messagePublisher;
    private final TrainingOutboxProperties properties;
    private final String workerId = TrainingOutboxRepository.newWorkerId();

    public TrainingOutboxPublisher(TrainingOutboxRepository repository,
                                   TrainingMessagePublisher messagePublisher,
                                   TrainingOutboxProperties properties) {
        this.repository = repository;
        this.messagePublisher = messagePublisher;
        this.properties = properties;
    }

    @Scheduled(fixedDelayString = "${training.outbox.fixed-delay-ms:1000}")
    public void publishPending() {
        if (!properties.isPublisherEnabled()) return;
        List<TrainingOutboxRepository.OutboxEvent> events = repository.claimPending(workerId);
        for (TrainingOutboxRepository.OutboxEvent event : events) {
            try {
                messagePublisher.publish(event);
                if (!repository.markPublished(event.id, workerId)) {
                    log.warn("训练计划 Outbox 已发送但状态更新失败，eventKey={}", event.eventKey);
                }
            } catch (Exception failure) {
                if (!repository.markFailed(event, workerId, failure)) {
                    log.error("训练计划 Outbox 失败状态更新失败，eventKey={}", event.eventKey, failure);
                } else {
                    log.warn("训练计划 Outbox 发布失败，将按策略重试，eventKey={}", event.eventKey, failure);
                }
            }
        }
    }
}

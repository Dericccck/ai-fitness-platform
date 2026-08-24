package com.shuyiwa.fitness.training.outbox;

import com.shuyiwa.fitness.training.config.TrainingOutboxProperties;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * 训练计划事件 Outbox 仓储。
 *
 * <p>状态变化和 Outbox 写入在同一个 MySQL 事务中完成；RabbitMQ 发布失败只影响 Outbox
 * 状态，不回滚已经提交的训练计划事实，后续由重试或人工补偿继续发送。</p>
 */
@Repository
public class TrainingOutboxRepository {
    private final JdbcTemplate jdbc;
    private final TrainingOutboxProperties properties;

    public TrainingOutboxRepository(JdbcTemplate jdbc, TrainingOutboxProperties properties) {
        this.jdbc = jdbc;
        this.properties = properties;
    }

    @Transactional
    public List<OutboxEvent> claimPending(String workerId) {
        Timestamp leaseExpiredAt = Timestamp.from(
                Instant.now().minusSeconds(properties.getClaimLeaseSeconds()));
        List<OutboxEvent> events = jdbc.query(
                "SELECT id, event_key, event_type, aggregate_id, organization_id, payload, attempt_count "
                        + "FROM agent_training_outbox WHERE status = 'PENDING' "
                        + "AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP) "
                        + "AND (claimed_at IS NULL OR claimed_at < ?) ORDER BY created_at, id LIMIT ? FOR UPDATE",
                new Object[]{leaseExpiredAt, properties.getBatchSize()},
                (rs, rowNum) -> new OutboxEvent(
                        rs.getLong("id"), rs.getString("event_key"), rs.getString("event_type"),
                        rs.getString("aggregate_id"), rs.getString("organization_id"),
                        rs.getString("payload"), rs.getInt("attempt_count"))
        );
        for (OutboxEvent event : events) {
            jdbc.update("UPDATE agent_training_outbox SET claimed_at = CURRENT_TIMESTAMP, claimed_by = ? "
                            + "WHERE id = ? AND status = 'PENDING'", workerId, event.id);
        }
        return events;
    }

    public boolean markPublished(long id, String workerId) {
        return jdbc.update("UPDATE agent_training_outbox SET status = 'PUBLISHED', "
                        + "published_at = CURRENT_TIMESTAMP, claimed_at = NULL, claimed_by = NULL, "
                        + "last_error = NULL WHERE id = ? AND status = 'PENDING' AND claimed_by = ?",
                id, workerId) == 1;
    }

    public boolean markFailed(OutboxEvent event, String workerId, Throwable failure) {
        int nextAttempt = event.attemptCount + 1;
        boolean dead = nextAttempt >= properties.getMaxAttempts();
        long retrySeconds = Math.min(300L,
                (long) properties.getRetryBaseSeconds() * Math.max(1, 1L << Math.min(nextAttempt - 1, 6)));
        String error = safeError(failure);
        return jdbc.update("UPDATE agent_training_outbox SET status = ?, attempt_count = ?, "
                        + "next_attempt_at = ?, last_error = ?, claimed_at = NULL, claimed_by = NULL "
                        + "WHERE id = ? AND status = 'PENDING' AND claimed_by = ?",
                dead ? "DEAD" : "PENDING", nextAttempt,
                dead ? null : Timestamp.from(Instant.now().plusSeconds(retrySeconds)), error,
                event.id, workerId) == 1;
    }

    private static String safeError(Throwable failure) {
        String message = failure == null ? "unknown outbox publish failure" : failure.toString();
        return message.length() <= 2000 ? message : message.substring(0, 2000);
    }

    public static final class OutboxEvent {
        public final long id;
        public final String eventKey;
        public final String eventType;
        public final String aggregateId;
        public final String organizationId;
        public final String payload;
        public final int attemptCount;

        public OutboxEvent(long id, String eventKey, String eventType, String aggregateId,
                           String organizationId, String payload, int attemptCount) {
            this.id = id;
            this.eventKey = eventKey;
            this.eventType = eventType;
            this.aggregateId = aggregateId;
            this.organizationId = organizationId;
            this.payload = payload;
            this.attemptCount = attemptCount;
        }
    }

    public static String newWorkerId() {
        return "training-outbox-" + UUID.randomUUID();
    }
}

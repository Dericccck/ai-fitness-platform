package com.shuyiwa.fitness.booking.outbox;

import com.shuyiwa.fitness.booking.config.BookingOutboxProperties;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * Booking Outbox 的持久化边界。
 *
 * <p>领取事件与业务写入分离：领取事务只负责设置租约，真正发布在事务外进行；发布成功后
 * 再原子更新为 PUBLISHED。发布器崩溃时租约过期，其他实例可以重新领取，避免长事务持有
 * 业务表锁。</p>
 */
@Repository
public class BookingOutboxRepository {
    private final JdbcTemplate jdbc;
    private final BookingOutboxProperties properties;

    public BookingOutboxRepository(JdbcTemplate jdbc, BookingOutboxProperties properties) {
        this.jdbc = jdbc;
        this.properties = properties;
    }

    @Transactional
    public List<OutboxEvent> claimPending(String workerId) {
        Timestamp leaseExpiredAt = Timestamp.from(
                Instant.now().minusSeconds(properties.getClaimLeaseSeconds()));
        List<OutboxEvent> events = jdbc.query(
                "SELECT id, event_key, event_type, aggregate_id, organization_id, payload, attempt_count, created_at "
                        + "FROM agent_booking_outbox WHERE status = 'PENDING' "
                        + "AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP) "
                        + "AND (claimed_at IS NULL OR claimed_at < ?) "
                        + "ORDER BY created_at, id LIMIT ? FOR UPDATE",
                new Object[]{leaseExpiredAt, properties.getBatchSize()},
                (rs, rowNum) -> new OutboxEvent(
                        rs.getLong("id"), rs.getString("event_key"), rs.getString("event_type"),
                        rs.getString("aggregate_id"), rs.getString("organization_id"),
                        rs.getString("payload"), rs.getInt("attempt_count"),
                        rs.getTimestamp("created_at").toInstant())
        );
        for (OutboxEvent event : events) {
            jdbc.update("UPDATE agent_booking_outbox SET claimed_at = CURRENT_TIMESTAMP, claimed_by = ? "
                            + "WHERE id = ? AND status = 'PENDING'",
                    workerId, event.id);
        }
        return events;
    }

    public boolean markPublished(long id, String workerId) {
        return jdbc.update("UPDATE agent_booking_outbox SET status = 'PUBLISHED', "
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
        return jdbc.update("UPDATE agent_booking_outbox SET status = ?, attempt_count = ?, "
                        + "next_attempt_at = ?, last_error = ?, claimed_at = NULL, claimed_by = NULL "
                        + "WHERE id = ? AND status = 'PENDING' AND claimed_by = ?",
                dead ? "DEAD" : "PENDING", nextAttempt,
                dead ? null : Timestamp.from(Instant.now().plusSeconds(retrySeconds)), error,
                event.id, workerId) == 1;
    }

    /** 返回待人工处置的 DEAD 事件；不会返回 payload，避免管理查询泄露业务正文。 */
    public List<DeadOutboxEvent> listDead() {
        return jdbc.query(
                "SELECT id, event_key, event_type, aggregate_id, organization_id, status, "
                        + "attempt_count, last_error, created_at, replay_count, last_replayed_by, "
                        + "last_replayed_at FROM agent_booking_outbox WHERE status = 'DEAD' "
                        + "ORDER BY created_at, id",
                (rs, rowNum) -> new DeadOutboxEvent(
                        rs.getLong("id"), rs.getString("event_key"), rs.getString("event_type"),
                        rs.getString("aggregate_id"), rs.getString("organization_id"),
                        rs.getString("status"), rs.getInt("attempt_count"), rs.getString("last_error"),
                        rs.getTimestamp("created_at").toInstant(), rs.getInt("replay_count"),
                        rs.getString("last_replayed_by"),
                        rs.getTimestamp("last_replayed_at") == null
                                ? null : rs.getTimestamp("last_replayed_at").toInstant())
        );
    }

    public Optional<DeadOutboxEvent> findDead(long id) {
        return listDead().stream().filter(event -> event.id == id).findFirst();
    }

    public Optional<DeadOutboxEvent> findById(long id) {
        List<DeadOutboxEvent> rows = jdbc.query(
                "SELECT id, event_key, event_type, aggregate_id, organization_id, status, "
                        + "attempt_count, last_error, created_at, replay_count, last_replayed_by, "
                        + "last_replayed_at FROM agent_booking_outbox WHERE id = ?",
                new Object[]{id},
                (rs, rowNum) -> new DeadOutboxEvent(
                        rs.getLong("id"), rs.getString("event_key"), rs.getString("event_type"),
                        rs.getString("aggregate_id"), rs.getString("organization_id"),
                        rs.getString("status"), rs.getInt("attempt_count"), rs.getString("last_error"),
                        rs.getTimestamp("created_at").toInstant(), rs.getInt("replay_count"),
                        rs.getString("last_replayed_by"),
                        rs.getTimestamp("last_replayed_at") == null
                                ? null : rs.getTimestamp("last_replayed_at").toInstant())
        );
        return rows.stream().findFirst();
    }

    /**
     * 受控重放 DEAD 事件。沿用原 event_key，重置尝试次数并记录操作人和原因；
     * 消费端可继续依赖 event_key 做幂等。
     */
    @Transactional
    public boolean replayDead(long id, String operatorId, String reason) {
        int updated = jdbc.update(
                "UPDATE agent_booking_outbox SET status = 'PENDING', attempt_count = 0, "
                        + "next_attempt_at = CURRENT_TIMESTAMP, last_error = NULL, claimed_at = NULL, "
                        + "claimed_by = NULL, replay_count = replay_count + 1, "
                        + "last_replayed_by = ?, last_replayed_at = CURRENT_TIMESTAMP "
                        + "WHERE id = ? AND status = 'DEAD'",
                operatorId, id);
        if (updated != 1) return false;
        jdbc.update(
                "INSERT INTO agent_booking_outbox_replay_audit "
                        + "(outbox_id, operator_id, reason, created_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                id, operatorId, reason);
        return true;
    }

    private static String safeError(Throwable failure) {
        String message = failure == null ? "未知的 Outbox 发布失败" : failure.toString();
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
        public final Instant occurredAt;

        public OutboxEvent(long id, String eventKey, String eventType, String aggregateId,
                           String organizationId, String payload, int attemptCount, Instant occurredAt) {
            this.id = id;
            this.eventKey = eventKey;
            this.eventType = eventType;
            this.aggregateId = aggregateId;
            this.organizationId = organizationId;
            this.payload = payload;
            this.attemptCount = attemptCount;
            this.occurredAt = occurredAt;
        }

        /** 兼容单元测试及旧适配器；生产读取始终使用 Outbox 的 created_at。 */
        public OutboxEvent(long id, String eventKey, String eventType, String aggregateId,
                           String organizationId, String payload, int attemptCount) {
            this(id, eventKey, eventType, aggregateId, organizationId, payload, attemptCount, Instant.EPOCH);
        }
    }

    public static final class DeadOutboxEvent {
        public final long id;
        public final String eventKey;
        public final String eventType;
        public final String aggregateId;
        public final String organizationId;
        public final String status;
        public final int attemptCount;
        public final String lastError;
        public final Instant createdAt;
        public final int replayCount;
        public final String lastReplayedBy;
        public final Instant lastReplayedAt;

        public DeadOutboxEvent(long id, String eventKey, String eventType, String aggregateId,
                               String organizationId, String status, int attemptCount, String lastError,
                               Instant createdAt, int replayCount, String lastReplayedBy,
                               Instant lastReplayedAt) {
            this.id = id;
            this.eventKey = eventKey;
            this.eventType = eventType;
            this.aggregateId = aggregateId;
            this.organizationId = organizationId;
            this.status = status;
            this.attemptCount = attemptCount;
            this.lastError = lastError;
            this.createdAt = createdAt;
            this.replayCount = replayCount;
            this.lastReplayedBy = lastReplayedBy;
            this.lastReplayedAt = lastReplayedAt;
        }
    }

    public static String newWorkerId() {
        return "booking-outbox-" + UUID.randomUUID();
    }
}

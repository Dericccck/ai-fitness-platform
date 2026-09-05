package com.shuyiwa.fitness.booking.api;

import java.time.Instant;

/** DEAD Outbox 的管理视图；故意不包含消息 payload。 */
public final class BookingDeadOutboxView {
    private final long id;
    private final String eventKey;
    private final String eventType;
    private final String aggregateId;
    private final String organizationId;
    private final String status;
    private final int attemptCount;
    private final String lastError;
    private final Instant createdAt;
    private final int replayCount;
    private final String lastReplayedBy;
    private final Instant lastReplayedAt;

    public BookingDeadOutboxView(long id, String eventKey, String eventType, String aggregateId,
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

    public long getId() { return id; }
    public String getEventKey() { return eventKey; }
    public String getEventType() { return eventType; }
    public String getAggregateId() { return aggregateId; }
    public String getOrganizationId() { return organizationId; }
    public String getStatus() { return status; }
    public int getAttemptCount() { return attemptCount; }
    public String getLastError() { return lastError; }
    public Instant getCreatedAt() { return createdAt; }
    public int getReplayCount() { return replayCount; }
    public String getLastReplayedBy() { return lastReplayedBy; }
    public Instant getLastReplayedAt() { return lastReplayedAt; }
}

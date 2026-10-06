package com.shuyiwa.fitness.training.api;

import java.time.Instant;

public final class TrainingDeadOutboxView {
    public final long id; public final String eventKey; public final String eventType;
    public final String aggregateId; public final String organizationId; public final String status;
    public final int attemptCount; public final String lastError; public final Instant createdAt;
    public final int replayCount; public final String lastReplayedBy; public final Instant lastReplayedAt;
    public TrainingDeadOutboxView(long id, String eventKey, String eventType, String aggregateId,
            String organizationId, String status, int attemptCount, String lastError, Instant createdAt,
            int replayCount, String lastReplayedBy, Instant lastReplayedAt) {
        this.id=id; this.eventKey=eventKey; this.eventType=eventType; this.aggregateId=aggregateId;
        this.organizationId=organizationId; this.status=status; this.attemptCount=attemptCount;
        this.lastError=lastError; this.createdAt=createdAt; this.replayCount=replayCount;
        this.lastReplayedBy=lastReplayedBy; this.lastReplayedAt=lastReplayedAt;
    }
}

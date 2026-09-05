package com.shuyiwa.fitness.booking.api;

/** DEAD 事件重放原因；原因会写入不可变审计表。 */
public final class BookingOutboxReplayRequest {
    private String reason;

    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }
}

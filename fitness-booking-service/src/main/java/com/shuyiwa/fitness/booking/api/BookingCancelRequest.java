package com.shuyiwa.fitness.booking.api;

import java.time.Instant;

/** Booking Agent 取消预约的稳定输入；expectedStartTime 用于防止确认后的并发覆盖。 */
public class BookingCancelRequest {
    private String organizationId;
    private String appointmentId;
    private Instant expectedStartTime;

    public String getOrganizationId() { return organizationId; }
    public void setOrganizationId(String organizationId) { this.organizationId = organizationId; }
    public String getAppointmentId() { return appointmentId; }
    public void setAppointmentId(String appointmentId) { this.appointmentId = appointmentId; }
    public Instant getExpectedStartTime() { return expectedStartTime; }
    public void setExpectedStartTime(Instant expectedStartTime) { this.expectedStartTime = expectedStartTime; }
}

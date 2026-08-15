package com.shuyiwa.fitness.booking.api;

import java.time.Instant;

/**
 * 改约参数。
 *
 * <p>v1 只允许调整已有预约的教练和时间，不跨合同、不换课程；expectedStartTime 是页面读取
 * 到的旧开始时间，服务会在更新条件中再次绑定它，防止用户确认期间预约被其他操作改写。</p>
 */
public class BookingRescheduleRequest {
    private String organizationId;
    private String appointmentId;
    private String coachId;
    private Instant expectedStartTime;
    private Instant startTime;
    private Instant endTime;

    public String getOrganizationId() { return organizationId; }
    public void setOrganizationId(String organizationId) { this.organizationId = organizationId; }
    public String getAppointmentId() { return appointmentId; }
    public void setAppointmentId(String appointmentId) { this.appointmentId = appointmentId; }
    public String getCoachId() { return coachId; }
    public void setCoachId(String coachId) { this.coachId = coachId; }
    public Instant getExpectedStartTime() { return expectedStartTime; }
    public void setExpectedStartTime(Instant expectedStartTime) { this.expectedStartTime = expectedStartTime; }
    public Instant getStartTime() { return startTime; }
    public void setStartTime(Instant startTime) { this.startTime = startTime; }
    public Instant getEndTime() { return endTime; }
    public void setEndTime(Instant endTime) { this.endTime = endTime; }
}

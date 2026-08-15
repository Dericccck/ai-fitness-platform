package com.shuyiwa.fitness.booking.api;

import java.time.Instant;

/** 创建预约后的稳定返回视图，不暴露旧 Appointment Entity 关系图。 */
public class BookingAppointmentView {
    private final String id;
    private final String organizationId;
    private final String userId;
    private final String coachId;
    private final String courseId;
    private final String courseName;
    private final Instant startTime;
    private final Instant endTime;
    private final Integer status;
    private final String contractId;
    private final Integer remainingClassHours;

    public BookingAppointmentView(String id, String organizationId, String userId, String coachId,
                                  String courseId, String courseName, Instant startTime, Instant endTime,
                                  Integer status, String contractId, Integer remainingClassHours) {
        this.id = id; this.organizationId = organizationId; this.userId = userId; this.coachId = coachId;
        this.courseId = courseId; this.courseName = courseName; this.startTime = startTime; this.endTime = endTime;
        this.status = status; this.contractId = contractId; this.remainingClassHours = remainingClassHours;
    }

    public String getId() { return id; }
    public String getOrganizationId() { return organizationId; }
    public String getUserId() { return userId; }
    public String getCoachId() { return coachId; }
    public String getCourseId() { return courseId; }
    public String getCourseName() { return courseName; }
    public Instant getStartTime() { return startTime; }
    public Instant getEndTime() { return endTime; }
    public Integer getStatus() { return status; }
    public String getContractId() { return contractId; }
    public Integer getRemainingClassHours() { return remainingClassHours; }
}

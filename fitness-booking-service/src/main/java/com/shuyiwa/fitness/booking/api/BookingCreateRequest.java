package com.shuyiwa.fitness.booking.api;

import java.time.Instant;

/** 创建预约的业务参数；确认声明和操作者身份不放进请求体。 */
public class BookingCreateRequest {
    private String organizationId;
    private String studentId;
    private String contractId;
    private String coachId;
    private String courseId;
    private Instant startTime;
    private Instant endTime;
    private Integer mark;

    public String getOrganizationId() { return organizationId; }
    public void setOrganizationId(String organizationId) { this.organizationId = organizationId; }
    public String getStudentId() { return studentId; }
    public void setStudentId(String studentId) { this.studentId = studentId; }
    public String getContractId() { return contractId; }
    public void setContractId(String contractId) { this.contractId = contractId; }
    public String getCoachId() { return coachId; }
    public void setCoachId(String coachId) { this.coachId = coachId; }
    public String getCourseId() { return courseId; }
    public void setCourseId(String courseId) { this.courseId = courseId; }
    public Instant getStartTime() { return startTime; }
    public void setStartTime(Instant startTime) { this.startTime = startTime; }
    public Instant getEndTime() { return endTime; }
    public void setEndTime(Instant endTime) { this.endTime = endTime; }
    public Integer getMark() { return mark; }
    public void setMark(Integer mark) { this.mark = mark; }
}

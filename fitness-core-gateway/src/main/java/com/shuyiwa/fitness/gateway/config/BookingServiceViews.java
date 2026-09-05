package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.api.ToolViews;

import java.time.Instant;

/** 预约写服务返回的内部 DTO，Gateway 再转换成稳定 Tool View。 */
final class BookingServiceViews {
    private BookingServiceViews() {}

    static class Appointment {
        public String id;
        public String organizationId;
        public String userId;
        public String coachId;
        public String courseId;
        public String courseName;
        public Instant startTime;
        public Instant endTime;
        public Integer status;
        public String contractId;
        public Integer remainingClassHours;

        ToolViews.BookingCreatedView toToolView() {
            return new ToolViews.BookingCreatedView(id, organizationId, userId, coachId, courseId,
                    courseName, startTime, endTime, status, contractId, remainingClassHours);
        }
    }

    static class CancelledAppointment {
        public String id;
        public String organizationId;
        public String userId;
        public String coachId;
        public String courseId;
        public String courseName;
        public Instant startTime;
        public Instant endTime;
        public Integer status;
        public String contractId;
        public Integer remainingClassHours;
        public boolean cancelled;

        ToolViews.BookingCancelledView toToolView() {
            return new ToolViews.BookingCancelledView(id, organizationId, userId, coachId, courseId,
                    courseName, startTime, endTime, status, contractId, remainingClassHours, cancelled);
        }
    }

    static class Operation {
        public String operationId;
        public String status;
        public Appointment appointment;
    }
}

package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.api.ToolViews;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.stream.Collectors;

/** 训练服务内部响应 DTO；通过显式转换隔离 Gateway 对外 Tool View。 */
final class TrainingServiceViews {

    private TrainingServiceViews() {}

    static final class Plan {
        public String id;
        public String organizationId;
        public String studentId;
        public String coachId;
        public String title;
        public String goalType;
        public String source;
        public String status;
        public int version;
        public String createdBy;
        public String reviewedBy;
        public String publishedBy;
        public String reviewComment;
        public Instant createdAt;
        public Instant updatedAt;
        public Instant reviewedAt;
        public Instant publishedAt;
        public List<Day> days;

        ToolViews.TrainingPlanView toToolView() {
            List<ToolViews.TrainingDayView> dayViews = days == null ? java.util.Collections.emptyList()
                    : days.stream().map(Day::toToolView).collect(Collectors.toList());
            return new ToolViews.TrainingPlanView(id, organizationId, studentId, coachId, title, goalType,
                    source, status, version, createdBy, reviewedBy, publishedBy, reviewComment, createdAt,
                    updatedAt, reviewedAt, publishedAt, dayViews);
        }
    }

    static final class Day {
        public String id;
        public Integer dayNumber;
        public String title;
        public LocalDate scheduledDate;
        public List<Item> items;

        ToolViews.TrainingDayView toToolView() {
            List<ToolViews.TrainingItemView> itemViews = items == null ? java.util.Collections.emptyList()
                    : items.stream().map(Item::toToolView).collect(Collectors.toList());
            return new ToolViews.TrainingDayView(id, dayNumber, title, scheduledDate, itemViews);
        }
    }

    static final class Item {
        public String id;
        public String exerciseName;
        public Integer sortOrder;
        public Integer sets;
        public String reps;
        public Integer restSeconds;
        public java.math.BigDecimal targetWeightKg;
        public java.math.BigDecimal targetRpe;
        public String notes;

        ToolViews.TrainingItemView toToolView() {
            return new ToolViews.TrainingItemView(id, exerciseName, sortOrder, sets, reps, restSeconds,
                    targetWeightKg, targetRpe, notes);
        }
    }

    static final class Execution {
        public String id;
        public String planId;
        public String dayId;
        public String organizationId;
        public String studentId;
        public String status;
        public LocalDate executionDate;
        public String note;
        public int version;
        public Instant createdAt;
        public Instant updatedAt;

        ToolViews.TrainingDayExecutionView toToolView() {
            return new ToolViews.TrainingDayExecutionView(id, planId, dayId, organizationId, studentId,
                    status, executionDate, note, version, createdAt, updatedAt);
        }
    }
}

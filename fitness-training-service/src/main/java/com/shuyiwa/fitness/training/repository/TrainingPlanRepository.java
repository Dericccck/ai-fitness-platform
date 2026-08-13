package com.shuyiwa.fitness.training.repository;

import com.shuyiwa.fitness.training.domain.TrainingDay;
import com.shuyiwa.fitness.training.domain.TrainingItem;
import com.shuyiwa.fitness.training.domain.TrainingPlan;
import com.shuyiwa.fitness.training.domain.TrainingPlanStatus;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Date;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * 训练计划显式 SQL 仓储。
 *
 * <p>训练服务不复用旧项目的 JPA Entity 图，因为旧图混入了不完整的赛事关系。这里用
 * 固定列、固定表和事务写入，确保结构化计划不会因为序列化关系意外暴露或修改其他业务。</p>
 */
@Repository
public class TrainingPlanRepository {

    private final JdbcTemplate jdbc;

    public TrainingPlanRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional
    public TrainingPlan insertDraft(TrainingPlan plan, String requestId) {
        Optional<TrainingPlan> existing = findByCreateRequestId(requestId);
        if (existing.isPresent()) {
            return existing.get();
        }
        int inserted = jdbc.update("INSERT IGNORE INTO training_plan "
                        + "(id, organization_id, student_id, coach_id, title, goal_type, source, status, "
                        + "version, created_by, create_request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                plan.getId(), plan.getOrganizationId(), plan.getStudentId(), plan.getCoachId(),
                plan.getTitle(), plan.getGoalType(), plan.getSource(), plan.getStatus().name(),
                plan.getCreatedBy(), requestId);
        if (inserted == 0) {
            // 并发请求在第一次查询后抢先插入时，由唯一键保证最终只保留一份草案。
            return findByCreateRequestId(requestId).orElseThrow(
                    () -> new IllegalStateException("创建请求幂等记录不存在"));
        }
        for (TrainingDay day : plan.getDays()) {
            jdbc.update("INSERT INTO training_plan_day "
                            + "(id, plan_id, day_number, title, scheduled_date) VALUES (?, ?, ?, ?, ?)",
                    day.getId(), plan.getId(), day.getDayNumber(), day.getTitle(),
                    day.getScheduledDate() == null ? null : Date.valueOf(day.getScheduledDate()));
            for (TrainingItem item : day.getItems()) {
                jdbc.update("INSERT INTO training_plan_item "
                                + "(id, day_id, exercise_name, sort_order, sets_count, reps, rest_seconds, "
                                + "target_weight_kg, target_rpe, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        item.getId(), day.getId(), item.getExerciseName(), item.getSortOrder(), item.getSets(),
                        item.getReps(), item.getRestSeconds(), item.getTargetWeightKg(), item.getTargetRpe(),
                        item.getNotes());
            }
        }
        return plan;
    }

    private Optional<TrainingPlan> findByCreateRequestId(String requestId) {
        List<String> ids = jdbc.query("SELECT id FROM training_plan WHERE create_request_id = ?",
                new Object[]{requestId}, (rs, rowNum) -> rs.getString("id"));
        return ids.isEmpty() ? Optional.empty() : findById(ids.get(0));
    }

    public Optional<TrainingPlan> findById(String planId) {
        List<TrainingPlan> plans = jdbc.query("SELECT id, organization_id, student_id, coach_id, title, goal_type, "
                        + "source, status, version, created_by, reviewed_by, published_by, review_comment, "
                        + "created_at, updated_at, reviewed_at, published_at FROM training_plan WHERE id = ?",
                new Object[]{planId}, (rs, rowNum) -> {
                    TrainingPlan plan = new TrainingPlan();
                    plan.setId(rs.getString("id"));
                    plan.setOrganizationId(rs.getString("organization_id"));
                    plan.setStudentId(rs.getString("student_id"));
                    plan.setCoachId(rs.getString("coach_id"));
                    plan.setTitle(rs.getString("title"));
                    plan.setGoalType(rs.getString("goal_type"));
                    plan.setSource(rs.getString("source"));
                    plan.setStatus(TrainingPlanStatus.valueOf(rs.getString("status")));
                    plan.setVersion(rs.getInt("version"));
                    plan.setCreatedBy(rs.getString("created_by"));
                    plan.setReviewedBy(rs.getString("reviewed_by"));
                    plan.setPublishedBy(rs.getString("published_by"));
                    plan.setReviewComment(rs.getString("review_comment"));
                    plan.setCreatedAt(toInstant(rs.getTimestamp("created_at")));
                    plan.setUpdatedAt(toInstant(rs.getTimestamp("updated_at")));
                    plan.setReviewedAt(toInstant(rs.getTimestamp("reviewed_at")));
                    plan.setPublishedAt(toInstant(rs.getTimestamp("published_at")));
                    return plan;
                });
        if (plans.isEmpty()) {
            return Optional.empty();
        }
        TrainingPlan plan = plans.get(0);
        List<TrainingDay> days = jdbc.query("SELECT id, day_number, title, scheduled_date FROM training_plan_day "
                        + "WHERE plan_id = ? ORDER BY day_number", new Object[]{planId}, (rs, rowNum) -> {
                    TrainingDay day = new TrainingDay();
                    day.setId(rs.getString("id"));
                    day.setDayNumber(rs.getInt("day_number"));
                    day.setTitle(rs.getString("title"));
                    Date date = rs.getDate("scheduled_date");
                    day.setScheduledDate(date == null ? null : date.toLocalDate());
                    return day;
                });
        for (TrainingDay day : days) {
            day.setItems(jdbc.query("SELECT id, exercise_name, sort_order, sets_count, reps, rest_seconds, "
                            + "target_weight_kg, target_rpe, notes FROM training_plan_item WHERE day_id = ? "
                            + "ORDER BY sort_order", new Object[]{day.getId()}, (rs, rowNum) -> {
                        TrainingItem item = new TrainingItem();
                        item.setId(rs.getString("id"));
                        item.setExerciseName(rs.getString("exercise_name"));
                        item.setSortOrder(rs.getInt("sort_order"));
                        item.setSets(rs.getInt("sets_count"));
                        item.setReps(rs.getString("reps"));
                        item.setRestSeconds((Integer) rs.getObject("rest_seconds"));
                        item.setTargetWeightKg(rs.getBigDecimal("target_weight_kg"));
                        item.setTargetRpe(rs.getBigDecimal("target_rpe"));
                        item.setNotes(rs.getString("notes"));
                        return item;
                    }));
        }
        plan.setDays(days);
        return Optional.of(plan);
    }

    /** 状态转换使用版本号条件，两个并发审核请求只能有一个成功。 */
    @Transactional
    public boolean transition(TrainingPlan plan, TrainingPlanStatus target, String action, String actorId,
                              String requestId, String comment) {
        Optional<String> appliedPlan = findPlanIdByRequest(requestId);
        if (appliedPlan.isPresent()) {
            // 网络重试不能再次写审计，也不能把同一请求误认为新的状态转换。
            return plan.getId().equals(appliedPlan.get());
        }
        int changed = jdbc.update("UPDATE training_plan SET status = ?, version = version + 1, "
                        + "reviewed_by = CASE WHEN ? IN ('APPROVED', 'REJECTED') THEN ? ELSE reviewed_by END, "
                        + "reviewed_at = CASE WHEN ? IN ('APPROVED', 'REJECTED') THEN CURRENT_TIMESTAMP ELSE reviewed_at END, "
                        + "published_by = CASE WHEN ? = 'PUBLISHED' THEN ? ELSE published_by END, "
                        + "published_at = CASE WHEN ? = 'PUBLISHED' THEN CURRENT_TIMESTAMP ELSE published_at END, "
                        + "review_comment = COALESCE(?, review_comment) "
                        + "WHERE id = ? AND status = ? AND version = ?",
                target.name(), target.name(), actorId, target.name(), target.name(), actorId, target.name(),
                comment, plan.getId(), plan.getStatus().name(), plan.getVersion());
        if (changed != 1) {
            return false;
        }
        jdbc.update("INSERT INTO training_plan_audit "
                        + "(plan_id, action, actor_id, request_id, from_status, to_status, comment) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?)",
                plan.getId(), action, actorId,
                requestId, plan.getStatus().name(), target.name(), comment);
        return true;
    }

    public boolean wasRequestApplied(String planId, String requestId) {
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(1) FROM training_plan_audit WHERE plan_id = ? AND request_id = ?",
                new Object[]{planId, requestId}, Integer.class);
        return count != null && count > 0;
    }

    private Optional<String> findPlanIdByRequest(String requestId) {
        try {
            return Optional.ofNullable(jdbc.queryForObject(
                    "SELECT plan_id FROM training_plan_audit WHERE request_id = ?",
                    new Object[]{requestId}, String.class));
        } catch (EmptyResultDataAccessException ignored) {
            return Optional.empty();
        }
    }

    public boolean isOrganizationMember(String organizationId, String userId) {
        return count("SELECT COUNT(1) FROM user_and_coach WHERE organization_id = ? AND user_id = ? "
                + "AND deleted = 0 AND status IN (0, 1, 4)", organizationId, userId) > 0;
    }

    public boolean isCoachForStudent(String organizationId, String coachId, String studentId) {
        return count("SELECT COUNT(1) FROM user_and_coach WHERE organization_id = ? AND coach_id = ? "
                + "AND user_id = ? AND deleted = 0 AND status = 1", organizationId, coachId, studentId) > 0;
    }

    private int count(String sql, String... args) {
        return jdbc.queryForObject(sql, args, Integer.class);
    }

    private static String id() {
        return UUID.randomUUID().toString().replace("-", "");
    }

    public static String newId() {
        return id();
    }

    private static Instant toInstant(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toInstant();
    }
}

package com.shuyiwa.fitness.gateway.repository;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Optional;

/**
 * 基于显式 SQL 的健身核心只读仓储。
 *
 * <p>这里不复用旧项目的 JPA Entity，原因是旧 Entity 图混入了赛事和活动关系，且会
 * 让 Agent 读接口意外暴露大量内部字段。所有查询都固定列名、固定租户过滤条件和
 * 最大返回条数；生产数据库账号还必须配置为只读账号。</p>
 */
@Repository
public class JdbcFitnessReadRepository implements FitnessReadRepository {

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public JdbcFitnessReadRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public Optional<ToolViews.UserView> findUser(String userId) {
        String sql = "SELECT id, name, phone, avatar, introduction, enabled "
                + "FROM login_user WHERE id = :userId LIMIT 1";
        List<ToolViews.UserView> result = jdbcTemplate.query(
                sql,
                new MapSqlParameterSource("userId", userId),
                (rs, rowNum) -> new ToolViews.UserView(
                        rs.getString("id"),
                        rs.getString("name"),
                        rs.getString("phone"),
                        rs.getString("avatar"),
                        rs.getString("introduction"),
                        rs.getBoolean("enabled")
                )
        );
        return result.stream().findFirst();
    }

    @Override
    public Optional<ToolViews.OrganizationView> findOrganization(String organizationId) {
        String sql = "SELECT id, name, address, summary, organization_type "
                + "FROM organization WHERE id = :organizationId LIMIT 1";
        List<ToolViews.OrganizationView> result = jdbcTemplate.query(
                sql,
                new MapSqlParameterSource("organizationId", organizationId),
                (rs, rowNum) -> new ToolViews.OrganizationView(
                        rs.getString("id"),
                        rs.getString("name"),
                        rs.getString("address"),
                        rs.getString("summary"),
                        rs.getString("organization_type")
                )
        );
        return result.stream().findFirst();
    }

    @Override
    public List<ToolViews.CourseView> findCourses(String organizationId, int limit) {
        String sql = "SELECT id, name, code, course_price, status FROM course "
                + "WHERE organization_id = :organizationId ORDER BY name, id LIMIT :limit";
        return jdbcTemplate.query(
                sql,
                new MapSqlParameterSource("organizationId", organizationId).addValue("limit", limit),
                (rs, rowNum) -> new ToolViews.CourseView(
                        rs.getString("id"),
                        rs.getString("name"),
                        rs.getString("code"),
                        rs.getObject("course_price", Integer.class),
                        rs.getObject("status", Integer.class)
                )
        );
    }

    @Override
    public List<ToolViews.ContractView> findContracts(String organizationId, String userId, int limit) {
        String sql = "SELECT id, organization_id, user_id, course_id, number_id, "
                + "contract_create_time, contract_end_time, class_hour, "
                + "remaining_class_hours, status FROM contract "
                + "WHERE organization_id = :organizationId AND user_id = :userId "
                + "AND deleted = 0 ORDER BY contract_end_time DESC, id DESC LIMIT :limit";
        return jdbcTemplate.query(
                sql,
                new MapSqlParameterSource("organizationId", organizationId)
                        .addValue("userId", userId)
                        .addValue("limit", limit),
                (rs, rowNum) -> new ToolViews.ContractView(
                        rs.getString("id"),
                        rs.getString("organization_id"),
                        rs.getString("user_id"),
                        rs.getString("course_id"),
                        rs.getString("number_id"),
                        rs.getString("contract_create_time"),
                        rs.getString("contract_end_time"),
                        rs.getObject("class_hour", Integer.class),
                        rs.getObject("remaining_class_hours", Integer.class),
                        rs.getObject("status", Integer.class)
                )
        );
    }

    @Override
    public List<ToolViews.AppointmentView> findAppointments(
            String organizationId,
            String userId,
            Instant from,
            Instant to,
            int limit
    ) {
        String sql = "SELECT id, organization_id, user_id, coach_id, course_id, course_name, "
                + "course_start_time, course_end_time, status, contract_id FROM appointment "
                + "WHERE organization_id = :organizationId AND user_id = :userId "
                + "AND deleted = 0 AND course_start_time >= :fromTime "
                + "AND course_start_time < :toTime ORDER BY course_start_time ASC LIMIT :limit";
        return jdbcTemplate.query(
                sql,
                new MapSqlParameterSource("organizationId", organizationId)
                        .addValue("userId", userId)
                        .addValue("fromTime", Timestamp.from(from))
                        .addValue("toTime", Timestamp.from(to))
                        .addValue("limit", limit),
                (rs, rowNum) -> new ToolViews.AppointmentView(
                        rs.getString("id"),
                        rs.getString("organization_id"),
                        rs.getString("user_id"),
                        rs.getString("coach_id"),
                        rs.getString("course_id"),
                        rs.getString("course_name"),
                        toInstant(rs.getTimestamp("course_start_time")),
                        toInstant(rs.getTimestamp("course_end_time")),
                        rs.getObject("status", Integer.class),
                        rs.getString("contract_id")
                )
        );
    }

    @Override
    public List<ToolViews.AppointmentView> findCoachAppointments(
            String organizationId,
            String coachId,
            Instant from,
            Instant to,
            String excludeAppointmentId,
            int limit
    ) {
        String sql = "SELECT id, organization_id, user_id, coach_id, course_id, course_name, "
                + "course_start_time, course_end_time, status, contract_id FROM appointment "
                + "WHERE organization_id = :organizationId AND deleted = 0 "
                + "AND (coach_id = :coachId OR temp_coach_id = :coachId) "
                + "AND status IN (0, 1, 3, 4, 5) "
                + "AND course_start_time < :toTime "
                + "AND (course_end_time IS NULL OR course_end_time > :fromTime) "
                + "AND (:excludeAppointmentId IS NULL OR id <> :excludeAppointmentId) "
                + "ORDER BY course_start_time ASC LIMIT :limit";
        return jdbcTemplate.query(
                sql,
                new MapSqlParameterSource("organizationId", organizationId)
                        .addValue("coachId", coachId)
                        .addValue("fromTime", Timestamp.from(from))
                        .addValue("toTime", Timestamp.from(to))
                        .addValue("excludeAppointmentId", excludeAppointmentId)
                        .addValue("limit", limit),
                (rs, rowNum) -> new ToolViews.AppointmentView(
                        rs.getString("id"),
                        rs.getString("organization_id"),
                        rs.getString("user_id"),
                        rs.getString("coach_id"),
                        rs.getString("course_id"),
                        rs.getString("course_name"),
                        toInstant(rs.getTimestamp("course_start_time")),
                        toInstant(rs.getTimestamp("course_end_time")),
                        rs.getObject("status", Integer.class),
                        rs.getString("contract_id")
                )
        );
    }

    @Override
    public List<LocalDate> findNonBusinessDays(String organizationId, LocalDate from, LocalDate to) {
        String sql = "SELECT body FROM system_settings "
                + "WHERE organization_id = :organizationId AND type = 'Nonbusiness_Day'";
        List<LocalDate> result = new ArrayList<>();
        jdbcTemplate.query(
                sql,
                new MapSqlParameterSource("organizationId", organizationId),
                (rs, rowNum) -> {
                    addDatesFromSettingBody(result, rs.getString("body"));
                    return null;
                }
        );
        result.removeIf(date -> date.isBefore(from) || date.isAfter(to));
        Collections.sort(result);
        return result;
    }

    @Override
    public List<LocalDate> findCoachVacationDays(String organizationId, String coachId, LocalDate from, LocalDate to) {
        String sql = "SELECT start_date, end_date FROM vacation_record "
                + "WHERE organization_id = :organizationId AND coach_id = :coachId "
                + "AND status = 0 AND end_date >= :fromDate AND start_date <= :toDate";
        List<LocalDate> result = new ArrayList<>();
        jdbcTemplate.query(
                sql,
                new MapSqlParameterSource("organizationId", organizationId)
                        .addValue("coachId", coachId)
                        .addValue("fromDate", java.sql.Date.valueOf(from))
                        .addValue("toDate", java.sql.Date.valueOf(to)),
                (rs, rowNum) -> {
                    java.sql.Date startDate = rs.getDate("start_date");
                    java.sql.Date endDate = rs.getDate("end_date");
                    if (startDate != null && endDate != null) {
                        LocalDate current = startDate.toLocalDate();
                        LocalDate last = endDate.toLocalDate();
                        while (!current.isAfter(last)) {
                            if (!current.isBefore(from) && !current.isAfter(to)) {
                                result.add(current);
                            }
                            current = current.plusDays(1);
                        }
                    }
                    return null;
                }
        );
        Collections.sort(result);
        return result;
    }

    @Override
    public boolean isCoachInOrganization(String organizationId, String coachId) {
        String sql = "SELECT COUNT(1) FROM login_user_authority "
                + "WHERE login_user_id = :coachId AND entity_id = :organizationId "
                + "AND authority = 'COACH'";
        Integer count = jdbcTemplate.queryForObject(
                sql,
                new MapSqlParameterSource("coachId", coachId).addValue("organizationId", organizationId),
                Integer.class
        );
        return count != null && count > 0;
    }

    /**
     * 旧系统把非营业日以 JSON 数组保存，历史数据可能是毫秒时间戳或日期字符串。
     * 这里只把它转成日期供预约预检使用；解析失败时不擅自判定为不可预约，写操作仍
     * 必须由原业务服务的最终校验兜底。
     */
    private static void addDatesFromSettingBody(List<LocalDate> result, String body) {
        if (body == null || body.trim().isEmpty()) {
            return;
        }
        String normalized = body.trim().replace("[", "").replace("]", "");
        for (String raw : normalized.split(",")) {
            String value = raw.trim().replace("\"", "");
            try {
                if (value.matches("\\d+")) {
                    result.add(Instant.ofEpochMilli(Long.parseLong(value)).atZone(ZoneOffset.UTC).toLocalDate());
                } else {
                    result.add(LocalDate.parse(value, DateTimeFormatter.ISO_LOCAL_DATE));
                }
            } catch (DateTimeParseException | NumberFormatException ignored) {
                // 兼容旧数据：单条设置解析失败不能让整个只读预检接口失败。
            }
        }
    }

    @Override
    public boolean isOrganizationMember(String organizationId, String userId) {
        return count("SELECT COUNT(1) FROM user_and_coach "
                + "WHERE organization_id = :organizationId AND user_id = :userId "
                + "AND deleted = 0 AND status IN (0, 1, 4)", organizationId, userId) > 0;
    }

    @Override
    public boolean isCoachForUser(String organizationId, String coachId, String userId) {
        return count("SELECT COUNT(1) FROM user_and_coach "
                + "WHERE organization_id = :organizationId AND user_id = :userId "
                + "AND coach_id = :coachId AND deleted = 0 AND status = 1",
                organizationId, userId, coachId) > 0;
    }

    private int count(String sql, String organizationId, String userId) {
        return jdbcTemplate.queryForObject(
                sql,
                new MapSqlParameterSource("organizationId", organizationId).addValue("userId", userId),
                Integer.class
        );
    }

    private int count(String sql, String organizationId, String userId, String coachId) {
        return jdbcTemplate.queryForObject(
                sql,
                new MapSqlParameterSource("organizationId", organizationId)
                        .addValue("userId", userId)
                        .addValue("coachId", coachId),
                Integer.class
        );
    }

    private static Instant toInstant(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toInstant();
    }
}

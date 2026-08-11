package com.shuyiwa.fitness.gateway.repository;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
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

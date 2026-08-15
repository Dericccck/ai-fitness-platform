package com.shuyiwa.fitness.gateway.repository;

import com.shuyiwa.fitness.gateway.operations.OperationsMetric;
import com.shuyiwa.fitness.gateway.operations.OperationsMetricRow;
import com.shuyiwa.fitness.gateway.operations.OperationsTimeBucket;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;

/**
 * Operations Agent 的固定 SQL 查询实现。
 *
 * <p>每个分支都绑定 organization_id、时间范围和已删除过滤条件，且只投影维度和聚合值。
 * 这里不是通用 SQL 执行器；后续即使接入 Text-to-SQL，也必须先落到同一指标目录和权限
 * 边界中，不能让模型直接拼接表名、列名或 WHERE 条件。</p>
 */
@Repository
public class JdbcOperationsReadRepository implements OperationsReadRepository {

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public JdbcOperationsReadRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public List<OperationsMetricRow> query(
            String organizationId,
            OperationsMetric metric,
            Instant from,
            Instant to,
            int limit
    ) {
        return query(organizationId, metric, OperationsTimeBucket.NONE, from, to, limit);
    }

    @Override
    public List<OperationsMetricRow> query(
            String organizationId,
            OperationsMetric metric,
            OperationsTimeBucket bucket,
            Instant from,
            Instant to,
            int limit
    ) {
        MapSqlParameterSource parameters = new MapSqlParameterSource()
                .addValue("organizationId", organizationId)
                .addValue("fromTime", Timestamp.from(from))
                .addValue("toTime", Timestamp.from(to))
                .addValue("limit", limit);
        if (bucket != OperationsTimeBucket.NONE) {
            if (metric != OperationsMetric.APPOINTMENT_COUNT) {
                throw new IllegalArgumentException(
                        "DAY/WEEK time buckets currently support APPOINTMENT_COUNT only");
            }
            return jdbcTemplate.query(
                    bucket == OperationsTimeBucket.DAY
                            ? "SELECT DATE_FORMAT(DATE(course_start_time), '%Y-%m-%d') AS dimension, "
                            + "DATE_FORMAT(DATE(course_start_time), '%Y-%m-%d') AS label, "
                            + "COUNT(1) AS value FROM appointment "
                            + "WHERE organization_id = :organizationId AND deleted = 0 "
                            + "AND course_start_time >= :fromTime AND course_start_time < :toTime "
                            + "GROUP BY DATE_FORMAT(DATE(course_start_time), '%Y-%m-%d') "
                            + "ORDER BY DATE_FORMAT(DATE(course_start_time), '%Y-%m-%d') ASC LIMIT :limit"
                            : "SELECT DATE_FORMAT(DATE_SUB(DATE(course_start_time), "
                            + "INTERVAL WEEKDAY(course_start_time) DAY), '%Y-%m-%d') AS dimension, "
                            + "DATE_FORMAT(DATE_SUB(DATE(course_start_time), "
                            + "INTERVAL WEEKDAY(course_start_time) DAY), '%Y-%m-%d') AS label, "
                            + "COUNT(1) AS value FROM appointment "
                            + "WHERE organization_id = :organizationId AND deleted = 0 "
                            + "AND course_start_time >= :fromTime AND course_start_time < :toTime "
                            + "GROUP BY DATE_FORMAT(DATE_SUB(DATE(course_start_time), "
                            + "INTERVAL WEEKDAY(course_start_time) DAY), '%Y-%m-%d') "
                            + "ORDER BY DATE_FORMAT(DATE_SUB(DATE(course_start_time), "
                            + "INTERVAL WEEKDAY(course_start_time) DAY), '%Y-%m-%d') ASC LIMIT :limit",
                    parameters, this::mapRow);
        }
        switch (metric) {
            case APPOINTMENT_COUNT:
                return jdbcTemplate.query(
                        "SELECT 'TOTAL' AS dimension, '预约总量' AS label, COUNT(1) AS value "
                                + "FROM appointment WHERE organization_id = :organizationId "
                                + "AND deleted = 0 AND course_start_time >= :fromTime "
                                + "AND course_start_time < :toTime",
                        parameters, this::mapRow);
            case APPOINTMENT_STATUS_BREAKDOWN:
                return jdbcTemplate.query(
                        "SELECT CAST(status AS CHAR) AS dimension, "
                                + "CASE status WHEN 0 THEN '待确认' WHEN 1 THEN '预约成功' "
                                + "WHEN 3 THEN '改课中' WHEN 4 THEN '已完成' WHEN 5 THEN '已取消' "
                                + "ELSE '其他状态' END AS label, COUNT(1) AS value "
                                + "FROM appointment WHERE organization_id = :organizationId "
                                + "AND deleted = 0 AND course_start_time >= :fromTime "
                                + "AND course_start_time < :toTime GROUP BY status "
                                + "ORDER BY value DESC, dimension ASC LIMIT :limit",
                        parameters, this::mapRow);
            case COURSE_APPOINTMENT_COUNT:
                return jdbcTemplate.query(
                        "SELECT course_id AS dimension, MAX(course_name) AS label, COUNT(1) AS value "
                                + "FROM appointment WHERE organization_id = :organizationId "
                                + "AND deleted = 0 AND course_start_time >= :fromTime "
                                + "AND course_start_time < :toTime GROUP BY course_id "
                                + "ORDER BY value DESC, dimension ASC LIMIT :limit",
                        parameters, this::mapRow);
            case COACH_APPOINTMENT_COUNT:
                return jdbcTemplate.query(
                        "SELECT coach_id AS dimension, MAX(coach_id) AS label, COUNT(1) AS value "
                                + "FROM appointment WHERE organization_id = :organizationId "
                                + "AND deleted = 0 AND course_start_time >= :fromTime "
                                + "AND course_start_time < :toTime GROUP BY coach_id "
                                + "ORDER BY value DESC, dimension ASC LIMIT :limit",
                        parameters, this::mapRow);
            case REMAINING_CLASS_HOURS:
                return jdbcTemplate.query(
                        "SELECT c.course_id AS dimension, MAX(co.name) AS label, "
                                + "COALESCE(SUM(c.remaining_class_hours), 0) AS value "
                                + "FROM contract c LEFT JOIN course co ON co.id = c.course_id "
                                + "WHERE c.organization_id = :organizationId AND c.deleted = 0 "
                                + "AND c.contract_create_time <= DATE(:toTime) "
                                + "AND (c.contract_end_time IS NULL OR c.contract_end_time >= DATE(:fromTime)) "
                                + "GROUP BY c.course_id ORDER BY value DESC, dimension ASC LIMIT :limit",
                        parameters, this::mapRow);
            default:
                throw new IllegalArgumentException("unsupported operations metric");
        }
    }

    private OperationsMetricRow mapRow(java.sql.ResultSet resultSet, int rowNum)
            throws java.sql.SQLException {
        return new OperationsMetricRow(
                resultSet.getString("dimension"),
                resultSet.getString("label"),
                resultSet.getLong("value")
        );
    }
}

package com.shuyiwa.fitness.gateway.repository;

import com.shuyiwa.fitness.gateway.operations.OperationsMetric;
import com.shuyiwa.fitness.gateway.operations.OperationsMetricRow;
import com.shuyiwa.fitness.gateway.operations.OperationsTimeBucket;
import org.junit.Assume;
import org.junit.Test;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.time.Instant;
import java.util.List;

import static org.junit.Assert.assertNotNull;

/**
 * Operations 固定指标的真实 MySQL 只读集成测试入口。
 *
 * <p>默认跳过，显式开启后复用已有业务事实库验证真实表字段和聚合 SQL。测试不写入数据，
 * 也不执行模型生成 SQL；它只验证指标目录中的固定查询可以在真实 MySQL 上运行。</p>
 */
public class JdbcOperationsReadRepositoryIntegrationTest {

    @Test
    public void readsAllFixedMetricQueries() {
        Assume.assumeTrue("true".equalsIgnoreCase(System.getenv("GATEWAY_IT_ENABLED")));
        DriverManagerDataSource dataSource = new DriverManagerDataSource(
                required("GATEWAY_IT_DB_URL"), required("GATEWAY_IT_DB_USERNAME"),
                required("GATEWAY_IT_DB_PASSWORD"));
        OperationsReadRepository repository = new JdbcOperationsReadRepository(
                new NamedParameterJdbcTemplate(dataSource));

        for (OperationsMetric metric : OperationsMetric.values()) {
            List<OperationsMetricRow> rows = repository.query(
                    required("GATEWAY_IT_ORGANIZATION_ID"), metric,
                    Instant.parse("2026-01-01T00:00:00Z"),
                    Instant.parse("2027-01-01T00:00:00Z"), 20
            );
            assertNotNull(metric.getCode() + " must return a list", rows);
        }
        for (OperationsTimeBucket bucket : new OperationsTimeBucket[]{
                OperationsTimeBucket.DAY, OperationsTimeBucket.WEEK
        }) {
            for (OperationsMetric metric : new OperationsMetric[]{
                    OperationsMetric.APPOINTMENT_COUNT,
                    OperationsMetric.COMPLETED_CLASS_COUNT,
                    OperationsMetric.NEW_CUSTOMER_COUNT,
                    OperationsMetric.COURSE_APPOINTMENT_COUNT,
                    OperationsMetric.COACH_APPOINTMENT_COUNT
            }) {
                List<OperationsMetricRow> rows = repository.query(
                        required("GATEWAY_IT_ORGANIZATION_ID"), metric, bucket,
                        Instant.parse("2026-01-01T00:00:00Z"),
                        Instant.parse("2027-01-01T00:00:00Z"), 100
                );
                assertNotNull(metric.getCode() + " " + bucket.getCode() + " must return a list", rows);
            }
        }
    }

    private static String required(String name) {
        String value = System.getenv(name);
        if (value == null || value.trim().isEmpty()) {
            throw new IllegalStateException(name + " is required when GATEWAY_IT_ENABLED=true");
        }
        return value;
    }
}

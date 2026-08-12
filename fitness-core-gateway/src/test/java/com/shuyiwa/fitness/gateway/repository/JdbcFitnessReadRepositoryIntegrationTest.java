package com.shuyiwa.fitness.gateway.repository;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import org.junit.Assume;
import org.junit.Test;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.util.List;
import java.util.Optional;

import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

/**
 * 真实 MySQL 只读集成测试入口。
 *
 * <p>该测试不会使用 mock 或内置假数据。只有显式设置 GATEWAY_IT_ENABLED=true、数据库
 * 连接和一个真实机构 ID 时才执行；普通本地测试缺少这些配置会被安全跳过。这样 CI
 * 不会误连开发库，同时为预发布环境提供可重复的真实表结构验证入口。</p>
 */
public class JdbcFitnessReadRepositoryIntegrationTest {

    @Test
    public void readsCoreOrganizationAndCourseTables() {
        Assume.assumeTrue("true".equalsIgnoreCase(System.getenv("GATEWAY_IT_ENABLED")));
        String url = required("GATEWAY_IT_DB_URL");
        String username = required("GATEWAY_IT_DB_USERNAME");
        String password = required("GATEWAY_IT_DB_PASSWORD");
        String organizationId = required("GATEWAY_IT_ORGANIZATION_ID");

        DriverManagerDataSource dataSource = new DriverManagerDataSource(url, username, password);
        FitnessReadRepository repository = new JdbcFitnessReadRepository(
                new NamedParameterJdbcTemplate(dataSource)
        );

        Optional<ToolViews.OrganizationView> organization = repository.findOrganization(organizationId);
        List<ToolViews.CourseView> courses = repository.findCourses(organizationId, 100);

        assertTrue("configured organization must exist", organization.isPresent());
        assertNotNull("course query must return a list", courses);
    }

    private static String required(String name) {
        String value = System.getenv(name);
        if (value == null || value.trim().isEmpty()) {
            throw new IllegalStateException(name + " is required when GATEWAY_IT_ENABLED=true");
        }
        return value;
    }
}

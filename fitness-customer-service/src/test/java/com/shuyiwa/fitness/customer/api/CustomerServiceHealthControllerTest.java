package com.shuyiwa.fitness.customer.api;

import com.shuyiwa.fitness.customer.config.CustomerServiceProperties;
import org.junit.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import static org.junit.Assert.assertEquals;

/** 存活探针只验证进程响应，不连接数据库或创建客服工单。 */
public class CustomerServiceHealthControllerTest {

    @Test
    public void liveProbeReturnsStableOkStatus() {
        CustomerServiceHealthController controller = new CustomerServiceHealthController(
                mock(JdbcTemplate.class), properties("internal-token")
        );

        CustomerServiceHealthController.HealthView result =
                controller.live();

        assertEquals("ok", result.getStatus());
    }

    @Test
    public void readyProbeRequiresDatabaseSchemaAndInternalToken() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.queryForObject("SELECT 1", Integer.class)).thenReturn(1);
        when(jdbc.queryForObject(
                eq("SELECT COUNT(1) FROM customer_service_schema_version WHERE version = ?"),
                any(Object[].class), eq(Integer.class)
        )).thenReturn(1);

        CustomerServiceHealthController.ReadyView result =
                new CustomerServiceHealthController(jdbc, properties("internal-token")).ready();

        assertEquals("ready", result.getStatus());
        assertEquals("ok", result.getChecks().get("database"));
        assertEquals("ok", result.getChecks().get("schema"));
        assertEquals("ok", result.getChecks().get("internal_token"));
    }

    @Test
    public void readyProbeFailsClosedWithoutTokenOrDatabase() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.queryForObject("SELECT 1", Integer.class))
                .thenThrow(new IllegalStateException("数据库不可用"));

        CustomerServiceHealthController.ReadyView result =
                new CustomerServiceHealthController(jdbc, properties(" ")).ready();

        assertEquals("not_ready", result.getStatus());
        assertEquals("failed", result.getChecks().get("database"));
        assertEquals("not_checked", result.getChecks().get("schema"));
        assertEquals("missing", result.getChecks().get("internal_token"));
    }

    private CustomerServiceProperties properties(String token) {
        CustomerServiceProperties properties = new CustomerServiceProperties();
        properties.setInternalServiceToken(token);
        return properties;
    }
}

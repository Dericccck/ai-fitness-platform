package com.shuyiwa.fitness.customer.api;

import com.shuyiwa.fitness.customer.config.CustomerServiceProperties;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 客服服务的进程存活探针。
 *
 * <p>该接口只确认 Spring Boot 进程可以响应，不访问 MySQL，也不代表客服工单业务已经
 * 就绪。数据库连接、内部 Token 和权限链路必须由后续的就绪检查或真实只读请求单独验证，
 * 避免把存活探针做成有副作用的业务接口。</p>
 */
@RestController
public class CustomerServiceHealthController {

    private final JdbcTemplate jdbc;
    private final CustomerServiceProperties properties;

    public CustomerServiceHealthController(JdbcTemplate jdbc, CustomerServiceProperties properties) {
        this.jdbc = jdbc;
        this.properties = properties;
    }

    @GetMapping("/health/live")
    public HealthView live() {
        return new HealthView("ok");
    }

    /**
     * 就绪探针只执行只读检查：数据库连接、最新客服表结构和内部 Token 是否配置。
     *
     * <p>这里不返回异常文本、不返回数据库地址，也不返回 Token 内容。健康探针可以被
     * 本地编排和部署平台调用，但不会因此暴露客服业务数据。</p>
     */
    @GetMapping("/health/ready")
    public ReadyView ready() {
        Map<String, String> checks = new LinkedHashMap<>();
        boolean databaseReady = checkDatabase(checks);
        boolean schemaReady = databaseReady && checkSchema(checks);
        boolean tokenReady = checkInternalToken(checks);
        boolean ready = databaseReady && schemaReady && tokenReady;
        return new ReadyView(ready ? "ready" : "not_ready", checks);
    }

    private boolean checkDatabase(Map<String, String> checks) {
        try {
            jdbc.queryForObject("SELECT 1", Integer.class);
            checks.put("database", "ok");
            return true;
        } catch (RuntimeException exception) {
            checks.put("database", "failed");
            checks.put("schema", "not_checked");
            return false;
        }
    }

    private boolean checkSchema(Map<String, String> checks) {
        try {
            Integer count = jdbc.queryForObject(
                    "SELECT COUNT(1) FROM customer_service_schema_version WHERE version = ?",
                    new Object[]{"V20260824_002"}, Integer.class);
            boolean present = count != null && count > 0;
            checks.put("schema", present ? "ok" : "failed");
            return present;
        } catch (RuntimeException exception) {
            checks.put("schema", "failed");
            return false;
        }
    }

    private boolean checkInternalToken(Map<String, String> checks) {
        boolean configured = properties.getInternalServiceToken() != null
                && !properties.getInternalServiceToken().trim().isEmpty();
        checks.put("internal_token", configured ? "ok" : "missing");
        return configured;
    }

    public static final class HealthView {
        private final String status;

        public HealthView(String status) {
            this.status = status;
        }

        public String getStatus() {
            return status;
        }
    }

    public static final class ReadyView {
        private final String status;
        private final Map<String, String> checks;

        public ReadyView(String status, Map<String, String> checks) {
            this.status = status;
            this.checks = checks;
        }

        public String getStatus() {
            return status;
        }

        public Map<String, String> getChecks() {
            return checks;
        }
    }
}

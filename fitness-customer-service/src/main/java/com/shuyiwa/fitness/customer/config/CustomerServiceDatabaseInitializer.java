package com.shuyiwa.fitness.customer.config;

import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;

/**
 * 本地客服表初始化器。
 *
 * <p>它只执行幂等的结构 SQL，当前不插入任何工单。生产环境应由独立迁移 Job 执行同一
 * 份版本化 SQL，并关闭 {@code schema-init-enabled}。</p>
 */
@Component
public class CustomerServiceDatabaseInitializer {

    private final DataSource dataSource;
    private final JdbcTemplate jdbc;
    private final CustomerServiceProperties properties;

    public CustomerServiceDatabaseInitializer(DataSource dataSource, JdbcTemplate jdbc,
                                              CustomerServiceProperties properties) {
        this.dataSource = dataSource;
        this.jdbc = jdbc;
        this.properties = properties;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void initialize() {
        if (!properties.isSchemaInitEnabled()) {
            return;
        }
        ResourceDatabasePopulator populator = new ResourceDatabasePopulator();
        populator.addScript(new ClassPathResource(
                "db/migration/V20260824_001__create_customer_service_ticket.sql"));
        populator.setContinueOnError(false);
        populator.execute(dataSource);
        // 读取版本用于日志/诊断，也能尽早暴露连接到了错误数据库的问题。
        jdbc.queryForObject("SELECT version FROM customer_service_schema_version WHERE version = ?",
                new Object[]{"V20260824_001"}, String.class);
    }
}

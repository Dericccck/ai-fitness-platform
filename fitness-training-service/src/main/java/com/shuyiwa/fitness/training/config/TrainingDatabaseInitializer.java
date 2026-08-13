package com.shuyiwa.fitness.training.config;

import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;

/**
 * 本地训练表初始化器。
 *
 * <p>当前环境尚未拉取 Flyway 依赖，因此先使用同一份版本化 SQL 做幂等初始化，避免
 * 为了“能跑起来”把建表语句散落在业务代码中。生产发布时应由迁移 Job 执行
 * {@code db/migration/V20260813_001__create_training_plan.sql} 并关闭此开关。</p>
 */
@Component
public class TrainingDatabaseInitializer {

    private final DataSource dataSource;
    private final JdbcTemplate jdbcTemplate;
    private final TrainingProperties properties;

    public TrainingDatabaseInitializer(DataSource dataSource, JdbcTemplate jdbcTemplate,
                                      TrainingProperties properties) {
        this.dataSource = dataSource;
        this.jdbcTemplate = jdbcTemplate;
        this.properties = properties;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void initialize() {
        if (!properties.isSchemaInitEnabled()) {
            return;
        }
        applyIfNeeded("V20260813_001", "db/migration/V20260813_001__create_training_plan.sql");
        applyIfNeeded("V20260813_002", "db/migration/V20260813_002__add_create_request_id.sql");
    }

    private void applyIfNeeded(String version, String resourcePath) {
        try {
            Integer count = jdbcTemplate.queryForObject(
                    "SELECT COUNT(1) FROM training_schema_version WHERE version = ?",
                    new Object[]{version}, Integer.class);
            if (count != null && count > 0) {
                return;
            }
        } catch (org.springframework.jdbc.BadSqlGrammarException ignored) {
            // V001 首次执行前版本表不存在，V001 脚本会负责创建版本表。
        }
        ResourceDatabasePopulator populator = new ResourceDatabasePopulator();
        populator.addScript(new ClassPathResource(resourcePath));
        populator.setContinueOnError(false);
        populator.execute(dataSource);
    }
}

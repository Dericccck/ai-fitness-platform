package com.shuyiwa.fitness.booking.config;

import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;

import javax.sql.DataSource;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;

/**
 * Booking Agent 数据库结构初始化器。
 *
 * <p>这里没有直接使用 Flyway，是因为当前项目的 Agent 模块仍处于从旧业务系统逐步
 * 增量接入阶段，需要在开发环境和已有业务库上安全重复启动。表创建使用
 * {@code CREATE TABLE IF NOT EXISTS}，而 MySQL 8 不支持
 * {@code ALTER TABLE ADD COLUMN IF NOT EXISTS}，所以列扩展必须先查询元数据再执行。
 * 这样既能兼容已有数据库，也不会因为服务重启重复加列。</p>
 */
public final class BookingDatabaseSchema {

    private BookingDatabaseSchema() {
    }

    /**
     * 执行 Booking Agent 所需的全部增量结构初始化。
     *
     * <p>SQL 文件按 UTF-8 读取非常重要：文件里包含中文表注释。如果依赖操作系统默认
     * 编码，数据库结构本身虽然可能创建成功，但 COMMENT 里的中文会被写成问号。</p>
     */
    public static void initialize(DataSource dataSource, JdbcTemplate jdbc) {
        ResourceDatabasePopulator populator = new ResourceDatabasePopulator();
        populator.addScript(new ClassPathResource(
                "db/migration/V20260815_001__create_booking_agent_tables.sql"));
        populator.addScript(new ClassPathResource(
                "db/migration/V20260815_002__create_booking_operation_tables.sql"));
        populator.addScript(new ClassPathResource(
                "db/migration/V20260905_003__create_outbox_replay_audit.sql"));
        populator.setSqlScriptEncoding(StandardCharsets.UTF_8.name());
        populator.setContinueOnError(false);
        populator.execute(dataSource);

        ensureOutboxColumns(jdbc);
        ensureOutboxClaimIndex(jdbc);
    }

    /**
     * 通过 information_schema 实现 MySQL 兼容的幂等加列。
     *
     * <p>每次只补一个缺失列，并且按依赖顺序执行；例如 next_attempt_at 的 AFTER
     * 位置依赖 attempt_count 已存在。已经存在的列会被跳过，不会修改线上数据。</p>
     */
    private static void ensureOutboxColumns(JdbcTemplate jdbc) {
        List<ColumnDefinition> columns = Arrays.asList(
                new ColumnDefinition("attempt_count",
                        "INT NOT NULL DEFAULT 0 AFTER status"),
                new ColumnDefinition("next_attempt_at",
                        "TIMESTAMP NULL AFTER attempt_count"),
                new ColumnDefinition("last_error",
                        "VARCHAR(2000) NULL AFTER next_attempt_at"),
                new ColumnDefinition("claimed_at",
                        "TIMESTAMP NULL AFTER last_error"),
                new ColumnDefinition("claimed_by",
                        "VARCHAR(128) NULL AFTER claimed_at"),
                new ColumnDefinition("replay_count",
                        "INT NOT NULL DEFAULT 0 AFTER claimed_by"),
                new ColumnDefinition("last_replayed_by",
                        "VARCHAR(128) NULL AFTER replay_count"),
                new ColumnDefinition("last_replayed_at",
                        "TIMESTAMP NULL AFTER last_replayed_by")
        );
        for (ColumnDefinition column : columns) {
            Integer count = jdbc.queryForObject(
                    "SELECT COUNT(1) FROM information_schema.columns "
                            + "WHERE table_schema = DATABASE() "
                            + "AND table_name = 'agent_booking_outbox' AND column_name = ?",
                    Integer.class, column.name);
            if (count != null && count == 0) {
                jdbc.execute("ALTER TABLE agent_booking_outbox ADD COLUMN "
                        + column.name + " " + column.definition);
            }
        }
    }

    private static void ensureOutboxClaimIndex(JdbcTemplate jdbc) {
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(1) FROM information_schema.statistics "
                        + "WHERE table_schema = DATABASE() AND table_name = 'agent_booking_outbox' "
                        + "AND index_name = 'idx_agent_booking_outbox_claim'", Integer.class);
        if (count != null && count == 0) {
            jdbc.execute("CREATE INDEX idx_agent_booking_outbox_claim "
                    + "ON agent_booking_outbox (status, next_attempt_at, claimed_at, created_at)");
        }
    }

    private static final class ColumnDefinition {
        private final String name;
        private final String definition;

        private ColumnDefinition(String name, String definition) {
            this.name = name;
            this.definition = definition;
        }
    }
}

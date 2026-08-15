package com.shuyiwa.fitness.booking.config;

import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;

/** 预约服务的幂等迁移入口；生产环境应由独立迁移 Job 执行同一份 SQL。 */
@Component
public class BookingDatabaseInitializer {

    private final DataSource dataSource;
    private final JdbcTemplate jdbc;
    private final BookingProperties properties;

    public BookingDatabaseInitializer(DataSource dataSource, JdbcTemplate jdbc, BookingProperties properties) {
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
        populator.addScript(new ClassPathResource("db/migration/V20260815_001__create_booking_agent_tables.sql"));
        populator.addScript(new ClassPathResource("db/migration/V20260815_002__extend_booking_outbox.sql"));
        populator.setContinueOnError(false);
        populator.execute(dataSource);
        ensureOutboxClaimIndex();
    }

    private void ensureOutboxClaimIndex() {
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(1) FROM information_schema.statistics "
                        + "WHERE table_schema = DATABASE() AND table_name = 'agent_booking_outbox' "
                        + "AND index_name = 'idx_agent_booking_outbox_claim'", Integer.class);
        if (count != null && count == 0) {
            // MySQL 不同版本对 CREATE INDEX IF NOT EXISTS 支持不一致，因此先查元数据，
            // 再创建固定名称索引，避免服务重复启动时因为重复索引导致初始化失败。
            jdbc.execute("CREATE INDEX idx_agent_booking_outbox_claim "
                    + "ON agent_booking_outbox (status, next_attempt_at, claimed_at, created_at)");
        }
    }
}

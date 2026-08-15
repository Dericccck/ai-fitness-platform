package com.shuyiwa.fitness.booking.config;

import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
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
        BookingDatabaseSchema.initialize(dataSource, jdbc);
    }
}

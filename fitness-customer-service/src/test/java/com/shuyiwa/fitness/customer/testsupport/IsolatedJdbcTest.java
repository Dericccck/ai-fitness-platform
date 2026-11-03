package com.shuyiwa.fitness.customer.testsupport;

import org.junit.After;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/** H2 by default; opt-in MySQL always creates and removes its own random databases. */
public abstract class IsolatedJdbcTest {
    private final List<String> databases = new ArrayList<>();
    private DriverManagerDataSource admin;

    protected DriverManagerDataSource newDataSource() {
        if (!"true".equalsIgnoreCase(System.getenv("MCP_MYSQL_IT_ENABLED"))) {
            return new DriverManagerDataSource("jdbc:h2:mem:" + UUID.randomUUID()
                    + ";MODE=MySQL;DB_CLOSE_DELAY=-1", "sa", "");
        }
        String port = required("MCP_MYSQL_IT_PORT");
        if (!port.matches("[0-9]{1,5}")) throw new IllegalArgumentException("Invalid isolated MySQL port");
        // The acceptance script owns this loopback-only server; no application DB URL is accepted.
        String prefix = "jdbc:mysql://127.0.0.1:" + port + "/";
        String options = "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC&characterEncoding=UTF-8";
        String password = required("MCP_MYSQL_IT_PASSWORD");
        admin = new DriverManagerDataSource(prefix + "mysql" + options, "root", password);
        String database = "codex_mcp_it_" + UUID.randomUUID().toString().replace("-", "");
        new JdbcTemplate(admin).execute("CREATE DATABASE " + database
                + " CHARACTER SET utf8mb4 COLLATE utf8mb4_bin");
        databases.add(database);
        return new DriverManagerDataSource(prefix + database + options, "root", password);
    }

    @After public void removeOwnedDatabases() {
        if (admin != null) {
            JdbcTemplate jdbc = new JdbcTemplate(admin);
            for (String database : databases) jdbc.execute("DROP DATABASE " + database);
            databases.clear();
        }
    }

    private static String required(String name) {
        String value = System.getenv(name);
        if (value == null || value.trim().isEmpty()) throw new IllegalStateException(name + " is required");
        return value;
    }
}

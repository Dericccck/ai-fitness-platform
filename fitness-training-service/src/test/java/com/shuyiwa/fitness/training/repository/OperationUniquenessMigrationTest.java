package com.shuyiwa.fitness.training.repository;

import org.junit.Before;
import org.junit.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import com.shuyiwa.fitness.training.testsupport.IsolatedJdbcTest;
import static org.junit.Assert.*;

public class OperationUniquenessMigrationTest extends IsolatedJdbcTest {
    private DriverManagerDataSource ds;
    private JdbcTemplate jdbc;
    @Before public void setUp() {
        ds = newDataSource();
        jdbc = new JdbcTemplate(ds);
        jdbc.execute("CREATE TABLE training_confirmation_consumption (request_id VARCHAR(128),tool_id VARCHAR(128))");
        jdbc.execute("CREATE TABLE training_schema_version (version VARCHAR(128) PRIMARY KEY)");
    }
    @Test public void uniqueOperationConstraintPreventsCrossToolReuse() {
        apply();
        jdbc.update("INSERT INTO training_confirmation_consumption VALUES (?,?)", "op-1", "tool-A");
        try {
            jdbc.update("INSERT INTO training_confirmation_consumption VALUES (?,?)", "op-1", "tool-B");
            fail("operation ID was reused across tools");
        } catch (DataAccessException expected) { }
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM training_confirmation_consumption", Integer.class).intValue());
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM training_schema_version WHERE version='V20261027_010'", Integer.class).intValue());
    }
    @Test public void historicalConflictAbortsWithoutDeletingEvidenceOrRecordingSuccess() {
        jdbc.update("INSERT INTO training_confirmation_consumption VALUES (?,?)", "op-1", "tool-A");
        jdbc.update("INSERT INTO training_confirmation_consumption VALUES (?,?)", "op-1", "tool-B");
        try { apply(); fail("ambiguous history silently migrated"); }
        catch (DataAccessException expected) { }
        assertEquals(2, jdbc.queryForObject("SELECT COUNT(*) FROM training_confirmation_consumption", Integer.class).intValue());
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM training_schema_version", Integer.class).intValue());
    }
    private void apply() {
        new ResourceDatabasePopulator(new ClassPathResource(
                "db/migration/V20261027_010__unique_confirmation_operation.sql")).execute(ds);
    }
}

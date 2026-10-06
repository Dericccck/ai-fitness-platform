package com.shuyiwa.fitness.booking.api;

import com.shuyiwa.fitness.booking.config.RuntimeLifecycle;
import org.junit.Test;
import org.springframework.context.event.ContextClosedEvent;
import org.springframework.context.support.StaticApplicationContext;
import org.springframework.jdbc.core.JdbcTemplate;
import static org.junit.Assert.assertEquals;
import static org.mockito.Mockito.*;

public class RuntimeReadinessControllerTest {
    @Test public void reportsReadyOnlyWhenDatabaseIsAvailable() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.queryForObject("SELECT 1", Integer.class)).thenReturn(1);
        RuntimeReadinessController controller = new RuntimeReadinessController(jdbc, new RuntimeLifecycle());
        assertEquals(200, controller.ready().getStatusCodeValue());
        when(jdbc.queryForObject("SELECT 1", Integer.class)).thenThrow(new IllegalStateException("private-connection"));
        assertEquals(503, controller.ready().getStatusCodeValue());
        assertEquals("not_ready", controller.ready().getBody().get("status"));
    }
    @Test public void drainFailsReadinessBeforeDependencyCleanup() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        RuntimeLifecycle lifecycle = new RuntimeLifecycle();
        lifecycle.onApplicationEvent(new ContextClosedEvent(new StaticApplicationContext()));
        assertEquals(503, new RuntimeReadinessController(jdbc, lifecycle).ready().getStatusCodeValue());
        verifyZeroInteractions(jdbc);
    }
}

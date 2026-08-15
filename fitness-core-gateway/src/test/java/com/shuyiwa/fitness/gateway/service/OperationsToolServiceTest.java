package com.shuyiwa.fitness.gateway.service;

import com.shuyiwa.fitness.gateway.api.OperationsViews;
import com.shuyiwa.fitness.gateway.operations.OperationsMetric;
import com.shuyiwa.fitness.gateway.operations.OperationsMetricRow;
import com.shuyiwa.fitness.gateway.repository.OperationsReadRepository;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import org.junit.Test;

import java.time.Instant;
import java.time.LocalDate;
import java.util.Arrays;
import java.util.HashSet;

import static org.junit.Assert.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

public class OperationsToolServiceTest {

    private final OperationsReadRepository repository = mock(OperationsReadRepository.class);
    private final OperationsToolService service = new OperationsToolService(repository);

    @Test
    public void studentCannotReadOperationsMetrics() {
        assertForbidden(() -> service.metric(
                context(AgentContext.ROLE_STUDENT), "org-1", "APPOINTMENT_COUNT",
                LocalDate.of(2026, 8, 1), LocalDate.of(2026, 8, 15), 20
        ));
    }

    @Test
    public void coachCannotReadOperationsMetrics() {
        assertForbidden(() -> service.metric(
                context(AgentContext.ROLE_COACH), "org-1", "APPOINTMENT_COUNT",
                LocalDate.of(2026, 8, 1), LocalDate.of(2026, 8, 15), 20
        ));
    }

    @Test
    public void organizationAdminReceivesFixedMetricRows() {
        when(repository.query(
                "org-1", OperationsMetric.APPOINTMENT_STATUS_BREAKDOWN,
                Instant.parse("2026-07-31T16:00:00Z"), Instant.parse("2026-08-15T16:00:00Z"), 20
        )).thenReturn(Arrays.asList(new OperationsMetricRow("1", "预约成功", 12)));

        OperationsViews.MetricView result = service.metric(
                context(AgentContext.ROLE_ORGANIZATION_ADMIN), "org-1", "APPOINTMENT_STATUS_BREAKDOWN",
                LocalDate.of(2026, 8, 1), LocalDate.of(2026, 8, 15), 20
        );

        assertEquals("APPOINTMENT_STATUS_BREAKDOWN", result.getMetric());
        assertEquals(1, result.getRows().size());
        assertEquals(12L, result.getRows().get(0).getValue());
    }

    @Test
    public void rangeLongerThanNinetyTwoDaysIsRejected() {
        try {
            service.metric(
                    context(AgentContext.ROLE_ORGANIZATION_ADMIN), "org-1", "APPOINTMENT_COUNT",
                    LocalDate.of(2026, 1, 1), LocalDate.of(2026, 4, 10), 20
            );
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError("oversized operations range must be rejected");
    }

    private static void assertForbidden(Runnable action) {
        try {
            action.run();
        } catch (GatewayForbiddenException expected) {
            return;
        }
        throw new AssertionError("non-admin operations access must be rejected");
    }

    private static AgentContext context(String role) {
        HashSet<String> organizations = new HashSet<>();
        organizations.add("org-1");
        HashSet<String> roles = new HashSet<>();
        roles.add(role);
        return new AgentContext(
                "user-1", organizations, roles,
                Instant.parse("2026-08-01T00:00:00Z"),
                Instant.parse("2026-08-01T00:05:00Z"), "nonce"
        );
    }
}

package com.shuyiwa.fitness.gateway.service;

import com.shuyiwa.fitness.gateway.api.OperationsViews;
import com.shuyiwa.fitness.gateway.operations.OperationsMetric;
import com.shuyiwa.fitness.gateway.operations.OperationsMetricRow;
import com.shuyiwa.fitness.gateway.operations.OperationsTimeBucket;
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
        assertEquals("NONE", result.getBucket());
    }

    @Test
    public void organizationAdminCanRequestDailyAppointmentTrend() {
        when(repository.query(
                "org-1", OperationsMetric.APPOINTMENT_COUNT, OperationsTimeBucket.DAY,
                Instant.parse("2026-07-31T16:00:00Z"), Instant.parse("2026-08-15T16:00:00Z"), 20
        )).thenReturn(Arrays.asList(
                new OperationsMetricRow("2026-08-01", "2026-08-01", 5),
                new OperationsMetricRow("2026-08-02", "2026-08-02", 8)
        ));

        OperationsViews.MetricView result = service.metric(
                context(AgentContext.ROLE_ORGANIZATION_ADMIN), "org-1", "APPOINTMENT_COUNT",
                LocalDate.of(2026, 8, 1), LocalDate.of(2026, 8, 15), 20, "DAY"
        );

        assertEquals("DAY", result.getBucket());
        assertEquals(2, result.getRows().size());
        assertEquals(8L, result.getRows().get(1).getValue());
    }

    @Test
    public void organizationAdminCanRequestCompletedClassTrend() {
        when(repository.query(
                "org-1", OperationsMetric.COMPLETED_CLASS_COUNT, OperationsTimeBucket.WEEK,
                Instant.parse("2026-08-02T16:00:00Z"), Instant.parse("2026-08-16T16:00:00Z"), 20
        )).thenReturn(Arrays.asList(
                new OperationsMetricRow("2026-08-03", "2026-08-03", 7),
                new OperationsMetricRow("2026-08-10", "2026-08-10", 9)
        ));

        OperationsViews.MetricView result = service.metric(
                context(AgentContext.ROLE_ORGANIZATION_ADMIN), "org-1", "COMPLETED_CLASS_COUNT",
                LocalDate.of(2026, 8, 3), LocalDate.of(2026, 8, 16), 20, "WEEK"
        );

        assertEquals("COMPLETED_CLASS_COUNT", result.getMetric());
        assertEquals("WEEK", result.getBucket());
        assertEquals(2, result.getRows().size());
        assertEquals(9L, result.getRows().get(1).getValue());
    }

    @Test
    public void organizationAdminCanRequestNewCustomerTrend() {
        when(repository.query(
                "org-1", OperationsMetric.NEW_CUSTOMER_COUNT, OperationsTimeBucket.DAY,
                Instant.parse("2026-08-01T16:00:00Z"), Instant.parse("2026-08-04T16:00:00Z"), 20
        )).thenReturn(Arrays.asList(
                new OperationsMetricRow("2026-08-02", "2026-08-02", 3),
                new OperationsMetricRow("2026-08-03", "2026-08-03", 5)
        ));

        OperationsViews.MetricView result = service.metric(
                context(AgentContext.ROLE_ORGANIZATION_ADMIN), "org-1", "NEW_CUSTOMER_COUNT",
                LocalDate.of(2026, 8, 2), LocalDate.of(2026, 8, 4), 20, "DAY"
        );

        assertEquals("NEW_CUSTOMER_COUNT", result.getMetric());
        assertEquals("DAY", result.getBucket());
        assertEquals(2, result.getRows().size());
        assertEquals(5L, result.getRows().get(1).getValue());
    }

    @Test
    public void organizationAdminCanRequestRevenueTrend() {
        when(repository.query(
                "org-1", OperationsMetric.REVENUE_AMOUNT, OperationsTimeBucket.WEEK,
                Instant.parse("2026-08-02T16:00:00Z"), Instant.parse("2026-08-16T16:00:00Z"), 20
        )).thenReturn(Arrays.asList(
                new OperationsMetricRow("2026-08-03", "2026-08-03", 12000),
                new OperationsMetricRow("2026-08-10", "2026-08-10", 18000)
        ));

        OperationsViews.MetricView result = service.metric(
                context(AgentContext.ROLE_ORGANIZATION_ADMIN), "org-1", "REVENUE_AMOUNT",
                LocalDate.of(2026, 8, 3), LocalDate.of(2026, 8, 16), 20, "WEEK"
        );

        assertEquals("REVENUE_AMOUNT", result.getMetric());
        assertEquals("WEEK", result.getBucket());
        assertEquals(2, result.getRows().size());
        assertEquals(18000L, result.getRows().get(1).getValue());
    }

    @Test
    public void organizationAdminCanRequestDailyCourseTrend() {
        when(repository.query(
                "org-1", OperationsMetric.COURSE_APPOINTMENT_COUNT, OperationsTimeBucket.DAY,
                Instant.parse("2026-07-31T16:00:00Z"), Instant.parse("2026-08-15T16:00:00Z"), 20
        )).thenReturn(Arrays.asList(
                new OperationsMetricRow("2026-08-01", "2026-08-01", 4),
                new OperationsMetricRow("2026-08-02", "2026-08-02", 6)
        ));

        OperationsViews.MetricView result = service.metric(
                context(AgentContext.ROLE_ORGANIZATION_ADMIN), "org-1", "COURSE_APPOINTMENT_COUNT",
                LocalDate.of(2026, 8, 1), LocalDate.of(2026, 8, 15), 20, "DAY"
        );

        assertEquals("DAY", result.getBucket());
        assertEquals(2, result.getRows().size());
        assertEquals(6L, result.getRows().get(1).getValue());
    }

    @Test
    public void organizationAdminCanRequestWeeklyCoachTrend() {
        when(repository.query(
                "org-1", OperationsMetric.COACH_APPOINTMENT_COUNT, OperationsTimeBucket.WEEK,
                Instant.parse("2026-07-31T16:00:00Z"), Instant.parse("2026-08-15T16:00:00Z"), 20
        )).thenReturn(Arrays.asList(
                new OperationsMetricRow("2026-07-27", "2026-07-27", 9),
                new OperationsMetricRow("2026-08-03", "2026-08-03", 12)
        ));

        OperationsViews.MetricView result = service.metric(
                context(AgentContext.ROLE_ORGANIZATION_ADMIN), "org-1", "COACH_APPOINTMENT_COUNT",
                LocalDate.of(2026, 8, 1), LocalDate.of(2026, 8, 15), 20, "WEEK"
        );

        assertEquals("WEEK", result.getBucket());
        assertEquals(2, result.getRows().size());
        assertEquals(12L, result.getRows().get(1).getValue());
    }

    @Test
    public void statusMetricCannotRequestTimeBucket() {
        try {
            service.metric(
                    context(AgentContext.ROLE_ORGANIZATION_ADMIN), "org-1",
                    "APPOINTMENT_STATUS_BREAKDOWN", LocalDate.of(2026, 8, 1),
                    LocalDate.of(2026, 8, 15), 20, "WEEK"
            );
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError("不支持的指标时间桶必须被拒绝");
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
        throw new AssertionError("超出范围的经营查询必须被拒绝");
    }

    private static void assertForbidden(Runnable action) {
        try {
            action.run();
        } catch (GatewayForbiddenException expected) {
            return;
        }
        throw new AssertionError("非管理员经营查询必须被拒绝");
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

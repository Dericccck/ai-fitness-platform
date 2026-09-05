package com.shuyiwa.fitness.gateway.service;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import com.shuyiwa.fitness.gateway.repository.FitnessReadRepository;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import org.junit.Test;

import java.time.Instant;
import java.time.LocalDate;
import java.time.Clock;
import java.time.ZoneOffset;
import java.util.Collections;
import java.util.HashSet;
import java.util.Optional;

import static org.junit.Assert.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

public class FitnessToolServiceTest {

    private final FitnessReadRepository repository = mock(FitnessReadRepository.class);
    private final FitnessToolService service = new FitnessToolService(
            repository,
            Clock.fixed(Instant.parse("2026-08-12T00:00:00Z"), ZoneOffset.UTC)
    );

    @Test
    public void studentCannotReadAnotherUserContract() {
        AgentContext context = context("user-1", AgentContext.ROLE_STUDENT);

        assertForbidden(() -> service.contracts(context, "org-1", "user-2", 20));
    }

    @Test
    public void organizationAdminCanReadUserContractInScopedOrganization() {
        AgentContext context = context("admin-1", AgentContext.ROLE_ORGANIZATION_ADMIN);
        when(repository.findContracts("org-1", "user-2", 20)).thenReturn(Collections.emptyList());

        assertEquals(
                0,
                service.contracts(context, "org-1", "user-2", 20).size()
        );
    }

    @Test
    public void coachMustBeAssignedBeforeReadingStudentAppointments() {
        AgentContext context = context("coach-1", AgentContext.ROLE_COACH);
        when(repository.isCoachForUser("org-1", "coach-1", "user-2")).thenReturn(false);

        assertForbidden(() -> service.appointments(
                context,
                "org-1",
                "user-2",
                Instant.parse("2026-08-12T00:00:00Z"),
                Instant.parse("2026-08-13T00:00:00Z"),
                20
        ));
    }

    @Test
    public void coachMustBeAssignedBeforeReadingStudentTrainingContext() {
        AgentContext context = context("coach-1", AgentContext.ROLE_COACH);
        when(repository.isCoachForUser("org-1", "coach-1", "student-1")).thenReturn(false);

        assertForbidden(() -> service.studentTrainingContextAccess(context, "org-1", "student-1"));
    }

    @Test
    public void assignedCoachReceivesScopedTrainingContextAccessReceipt() {
        AgentContext context = context("coach-1", AgentContext.ROLE_COACH);
        when(repository.isCoachForUser("org-1", "coach-1", "student-1")).thenReturn(true);
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);

        ToolViews.StudentTrainingContextAccessView result =
                service.studentTrainingContextAccess(context, "org-1", "student-1");

        assertEquals("coach-1", result.getActorId());
        assertEquals("student-1", result.getStudentId());
        assertEquals("ASSIGNED_COACH", result.getAccessType());
    }

    @Test
    public void administratorCannotReadTrainingContextForNonMember() {
        AgentContext context = context("admin-1", AgentContext.ROLE_ORGANIZATION_ADMIN);
        when(repository.isOrganizationMember("org-1", "student-outside")).thenReturn(false);

        assertForbidden(() -> service.studentTrainingContextAccess(
                context, "org-1", "student-outside"
        ));
    }

    @Test
    public void studentCannotReadOrganizationOutsideSignedScope() {
        AgentContext context = context("user-1", AgentContext.ROLE_STUDENT);

        assertForbidden(() -> service.courses(context, "org-2", 20));
    }

    @Test
    public void bookingAvailabilityReportsCoachConflictWithoutWriting() {
        AgentContext context = context("student-1", AgentContext.ROLE_STUDENT);
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);
        when(repository.isCoachInOrganization("org-1", "coach-1")).thenReturn(true);
        when(repository.findCoachAppointments(
                "org-1",
                "coach-1",
                Instant.parse("2026-08-20T10:00:00Z"),
                Instant.parse("2026-08-20T11:00:00Z"),
                null,
                20
        )).thenReturn(Collections.emptyList());
        when(repository.findCoachVacationDays(
                "org-1",
                "coach-1",
                LocalDate.of(2026, 8, 20),
                LocalDate.of(2026, 8, 20)
        )).thenReturn(Collections.emptyList());
        when(repository.findNonBusinessDays(
                "org-1",
                LocalDate.of(2026, 8, 20),
                LocalDate.of(2026, 8, 20)
        )).thenReturn(Collections.emptyList());

        ToolViews.BookingAvailabilityView result = service.bookingAvailability(
                context,
                "org-1",
                null,
                "coach-1",
                "course-1",
                Instant.parse("2026-08-20T10:00:00Z"),
                Instant.parse("2026-08-20T11:00:00Z"),
                null
        );

        assertEquals(true, result.isAvailable());
        assertEquals(0, result.getConflicts().size());
    }

    private static void assertForbidden(Runnable action) {
        try {
            action.run();
        } catch (GatewayForbiddenException expected) {
            return;
        }
        throw new AssertionError("out-of-scope access must be rejected");
    }

    private static AgentContext context(String userId, String role) {
        HashSet<String> organizations = new HashSet<>();
        organizations.add("org-1");
        HashSet<String> roles = new HashSet<>();
        roles.add(role);
        return new AgentContext(
                userId,
                organizations,
                roles,
                Instant.parse("2026-08-12T00:00:00Z"),
                Instant.parse("2026-08-12T00:05:00Z"),
                "nonce"
        );
    }
}

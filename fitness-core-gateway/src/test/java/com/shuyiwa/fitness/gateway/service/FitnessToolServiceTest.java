package com.shuyiwa.fitness.gateway.service;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import com.shuyiwa.fitness.gateway.repository.FitnessReadRepository;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import org.junit.Test;

import java.time.Instant;
import java.util.Collections;
import java.util.HashSet;
import java.util.Optional;

import static org.junit.Assert.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

public class FitnessToolServiceTest {

    private final FitnessReadRepository repository = mock(FitnessReadRepository.class);
    private final FitnessToolService service = new FitnessToolService(repository);

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
    public void studentCannotReadOrganizationOutsideSignedScope() {
        AgentContext context = context("user-1", AgentContext.ROLE_STUDENT);

        assertForbidden(() -> service.courses(context, "org-2", 20));
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

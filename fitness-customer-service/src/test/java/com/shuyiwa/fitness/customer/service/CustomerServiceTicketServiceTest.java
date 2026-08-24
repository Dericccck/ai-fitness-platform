package com.shuyiwa.fitness.customer.service;

import com.shuyiwa.fitness.customer.api.CustomerServiceTicketView;
import com.shuyiwa.fitness.customer.repository.CustomerServiceTicketRepository;
import com.shuyiwa.fitness.customer.security.CustomerServiceActor;
import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

import static org.junit.Assert.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 工单查询的组织范围和主体范围回归测试。 */
public class CustomerServiceTicketServiceTest {

    private final CustomerServiceTicketRepository repository = mock(CustomerServiceTicketRepository.class);
    private final CustomerServiceTicketService service = new CustomerServiceTicketService(repository);

    @Test
    public void studentQueryIsForcedToOwnUser() {
        when(repository.find("org-1", "student-1", null, 20))
                .thenReturn(Collections.<CustomerServiceTicketView>emptyList());

        assertEquals(0, service.list(actor("student-1", "STUDENT"), "org-1", null, null, 20).size());
        verify(repository).find("org-1", "student-1", null, 20);
    }

    @Test
    public void administratorCanQueryOrganizationTickets() {
        CustomerServiceTicketView ticket = new CustomerServiceTicketView();
        ticket.setId("ticket-1");
        when(repository.find("org-1", "student-2", "OPEN", 10)).thenReturn(Arrays.asList(ticket));

        assertEquals(1, service.list(actor("admin-1", "ORGANIZATION_ADMIN"), "org-1",
                "student-2", "OPEN", 10).size());
        verify(repository).find("org-1", "student-2", "OPEN", 10);
    }

    @Test(expected = org.springframework.web.server.ResponseStatusException.class)
    public void studentCannotQueryAnotherUser() {
        service.list(actor("student-1", "STUDENT"), "org-1", "student-2", null, 20);
    }

    @Test(expected = org.springframework.web.server.ResponseStatusException.class)
    public void actorCannotQueryOrganizationOutsideScope() {
        service.list(actor("student-1", "STUDENT"), "org-2", null, null, 20);
    }

    private static CustomerServiceActor actor(String userId, String role) {
        return new CustomerServiceActor(userId,
                Collections.singleton(role), Collections.singleton("org-1"), "request-1");
    }
}

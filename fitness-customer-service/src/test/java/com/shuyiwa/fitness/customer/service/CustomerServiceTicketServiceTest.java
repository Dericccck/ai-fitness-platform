package com.shuyiwa.fitness.customer.service;

import com.shuyiwa.fitness.customer.api.CustomerServiceTicketView;
import com.shuyiwa.fitness.customer.api.CustomerServiceTicketCreateRequest;
import com.shuyiwa.fitness.customer.repository.CustomerServiceTicketRepository;
import com.shuyiwa.fitness.customer.security.CustomerServiceActor;
import com.shuyiwa.fitness.customer.security.CustomerServiceConfirmation;
import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

import static org.junit.Assert.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.any;
import static org.mockito.Mockito.eq;
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

    @Test(expected = org.springframework.web.server.ResponseStatusException.class)
    public void ticketCreationRequiresConfirmation() {
        CustomerServiceTicketCreateRequest request = createRequest();
        service.create(actor("student-1", "STUDENT"), request);
    }

    @Test
    public void administratorWithoutSubjectDefaultsToSignedActor() {
        CustomerServiceTicketCreateRequest request = createRequest();
        CustomerServiceTicketView created = new CustomerServiceTicketView();
        created.setId("ticket-1");
        CustomerServiceConfirmation confirmation = new CustomerServiceConfirmation(
                "confirmation-1", "jti-1", "fitness.support.ticket.create.v1",
                "CREATE_CUSTOMER_SERVICE_TICKET", "org-1", "org-1:admin-1",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        when(repository.insert(any(CustomerServiceActor.class), eq(request), eq("admin-1")))
                .thenReturn(created);

        assertEquals("ticket-1", service.create(new CustomerServiceActor(
                "admin-1", Collections.singleton("ORGANIZATION_ADMIN"),
                Collections.singleton("org-1"), "request-1", confirmation), request).getId());
        verify(repository).insert(any(CustomerServiceActor.class), eq(request), eq("admin-1"));
    }

    private static CustomerServiceTicketCreateRequest createRequest() {
        CustomerServiceTicketCreateRequest request = new CustomerServiceTicketCreateRequest();
        request.setOrganizationId("org-1");
        request.setCategory("GENERAL");
        request.setSubject("需要帮助");
        request.setDescription("测试客服工单");
        return request;
    }

    private static CustomerServiceActor actor(String userId, String role) {
        return new CustomerServiceActor(userId,
                Collections.singleton(role), Collections.singleton("org-1"), "request-1");
    }
}

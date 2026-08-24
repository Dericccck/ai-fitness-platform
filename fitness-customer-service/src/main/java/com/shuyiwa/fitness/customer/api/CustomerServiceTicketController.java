package com.shuyiwa.fitness.customer.api;

import com.shuyiwa.fitness.customer.security.CustomerServiceActor;
import com.shuyiwa.fitness.customer.service.CustomerServiceTicketService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/** 客服工单只读内部 API；本轮没有 POST，因此不会产生真实工单。 */
@RestController
@RequestMapping("/internal/customer-service/v1/tickets")
public class CustomerServiceTicketController {

    private final CustomerServiceTicketService service;

    public CustomerServiceTicketController(CustomerServiceTicketService service) {
        this.service = service;
    }

    @GetMapping
    public List<CustomerServiceTicketView> list(CustomerServiceActor actor,
                                                @RequestParam String organizationId,
                                                @RequestParam(required = false) String subjectUserId,
                                                @RequestParam(required = false) String status,
                                                @RequestParam(required = false, defaultValue = "20") int limit) {
        int safeLimit = Math.max(1, Math.min(limit, 100));
        return service.list(actor, organizationId, subjectUserId, status, safeLimit);
    }

    @GetMapping("/{ticketId}")
    public CustomerServiceTicketView get(CustomerServiceActor actor,
                                         @RequestParam String organizationId,
                                         @PathVariable String ticketId) {
        return service.get(actor, organizationId, ticketId);
    }
}

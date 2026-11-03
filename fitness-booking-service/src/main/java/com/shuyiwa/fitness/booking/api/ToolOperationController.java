package com.shuyiwa.fitness.booking.api;

import com.shuyiwa.fitness.booking.service.ToolOperationQueryService;
import com.shuyiwa.fitness.booking.security.BookingActor;
import org.springframework.web.bind.annotation.*;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

/** Covered by the existing internal-token and trusted-actor interceptor. No execution capability. */
@RestController
@RequestMapping("/internal/booking/v1/tool-operations")
public class ToolOperationController {
    private final ToolOperationQueryService service;
    public ToolOperationController(ToolOperationQueryService service) { this.service = service; }
    @GetMapping("/{operationId}")
    public ToolOperationView query(BookingActor actor, @PathVariable String operationId,
            @RequestParam String toolId, @RequestParam String confirmationId,
            @RequestParam String organizationId, @RequestParam String payloadHash) {
        if (!actor.canAccessOrganization(organizationId)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "Organization outside scope");
        }
        return service.query(operationId, toolId, confirmationId, organizationId, actor.getUserId(), payloadHash);
    }
}

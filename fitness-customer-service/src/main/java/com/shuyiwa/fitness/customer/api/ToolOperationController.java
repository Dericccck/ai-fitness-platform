package com.shuyiwa.fitness.customer.api;

import com.shuyiwa.fitness.customer.service.ToolOperationQueryService;
import com.shuyiwa.fitness.customer.security.CustomerServiceActor;
import org.springframework.web.bind.annotation.*;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

/** Covered by the existing internal-token and trusted-actor interceptor. No execution capability. */
@RestController
@RequestMapping("/internal/customer-service/v1/tool-operations")
public class ToolOperationController {
    private final ToolOperationQueryService service;
    public ToolOperationController(ToolOperationQueryService service) { this.service = service; }
    @GetMapping("/{operationId}")
    public ToolOperationView query(CustomerServiceActor actor, @PathVariable String operationId,
            @RequestParam String toolId, @RequestParam String confirmationId,
            @RequestParam String organizationId, @RequestParam String payloadHash) {
        if (!actor.canAccessOrganization(organizationId)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "Organization outside scope");
        }
        return service.query(operationId, toolId, confirmationId, organizationId, actor.getUserId(), payloadHash);
    }
}

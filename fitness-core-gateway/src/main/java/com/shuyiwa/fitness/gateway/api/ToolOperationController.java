package com.shuyiwa.fitness.gateway.api;

import com.shuyiwa.fitness.gateway.config.ToolOperationClient;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import com.shuyiwa.fitness.gateway.security.InternalServiceTokenVerifier;
import org.springframework.web.bind.annotation.*;

/** User queries and offline reconciliation use separate authenticated entry points. */
@RestController
public class ToolOperationController {
    private final ToolOperationClient client;
    private final InternalServiceTokenVerifier verifier;
    public ToolOperationController(ToolOperationClient client, InternalServiceTokenVerifier verifier) {
        this.client = client; this.verifier = verifier;
    }
    @GetMapping("/internal/agent-tools/v1/operations/{operationId}")
    public ToolOperationView query(AgentContext context, @PathVariable String operationId,
            @RequestParam String toolId, @RequestParam String confirmationId,
            @RequestParam String organizationId, @RequestParam String payloadHash) {
        if (!context.canAccessOrganization(organizationId)) throw new GatewayForbiddenException("操作机构不在授权范围内");
        return client.query(operationId, toolId, confirmationId, organizationId, context.getSubjectUserId(), payloadHash);
    }
    @PostMapping("/internal/agent-reconciliation/v1/operations/{operationId}")
    public ToolOperationView reconcile(@RequestHeader("X-Internal-Service-Token") String token,
            @PathVariable String operationId, @RequestBody OperationScope scope) {
        verifier.verify(token, InternalServiceTokenVerifier.RECONCILE);
        if (scope == null) throw new IllegalArgumentException("Missing operation scope");
        return client.query(operationId, scope.getToolId(), scope.getConfirmationId(),
                scope.getOrganizationId(), scope.getActorId(), scope.getPayloadHash());
    }
    public static final class OperationScope {
        private String toolId, confirmationId, organizationId, actorId, payloadHash;
        public String getToolId() { return toolId; }
        public void setToolId(String value) { toolId = value; }
        public String getConfirmationId() { return confirmationId; }
        public void setConfirmationId(String value) { confirmationId = value; }
        public String getOrganizationId() { return organizationId; }
        public void setOrganizationId(String value) { organizationId = value; }
        public String getActorId() { return actorId; }
        public void setActorId(String value) { actorId = value; }
        public String getPayloadHash() { return payloadHash; }
        public void setPayloadHash(String value) { payloadHash = value; }
    }
}

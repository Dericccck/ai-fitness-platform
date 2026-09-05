package com.shuyiwa.fitness.gateway.api;

import com.shuyiwa.fitness.gateway.config.BookingServiceClient;
import com.shuyiwa.fitness.gateway.security.InternalServiceTokenVerifier;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 后台结果对账入口；使用服务身份，并把结果绑定到确认单保存的机构和原始操作者。 */
@RestController
@RequestMapping("/internal/agent-reconciliation/v1")
public class AgentReconciliationController {
    private final InternalServiceTokenVerifier tokenVerifier;
    private final BookingServiceClient bookingServiceClient;

    public AgentReconciliationController(InternalServiceTokenVerifier tokenVerifier,
                                         BookingServiceClient bookingServiceClient) {
        this.tokenVerifier = tokenVerifier;
        this.bookingServiceClient = bookingServiceClient;
    }

    @PostMapping("/booking/operations/{operationId}")
    public ToolViews.BookingOperationView reconcileBookingOperation(
            @RequestHeader("X-Internal-Service-Token") String internalToken,
            @PathVariable String operationId,
            @RequestBody ReconciliationScope scope) {
        tokenVerifier.verify(internalToken);
        if (scope == null || isBlank(scope.getOrganizationId()) || isBlank(scope.getActorId())) {
            throw new IllegalArgumentException("对账机构和原始操作者不能为空");
        }
        return bookingServiceClient.queryOperationForReconciliation(
                operationId, scope.getOrganizationId(), scope.getActorId());
    }

    private static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    public static final class ReconciliationScope {
        private String organizationId;
        private String actorId;

        public String getOrganizationId() { return organizationId; }
        public void setOrganizationId(String organizationId) { this.organizationId = organizationId; }
        public String getActorId() { return actorId; }
        public void setActorId(String actorId) { this.actorId = actorId; }
    }
}

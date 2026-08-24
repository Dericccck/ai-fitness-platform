package com.shuyiwa.fitness.customer.security;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/** Gateway 已验证后传入的客服查询主体；工单服务不信任查询参数中的用户身份。 */
public final class CustomerServiceActor {

    public static final String SYSTEM_ADMIN = "SYSTEM_ADMIN";
    public static final String ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN";

    private final String userId;
    private final Set<String> roles;
    private final Set<String> organizationIds;
    private final String requestId;
    private final CustomerServiceConfirmation confirmation;

    public CustomerServiceActor(String userId, Set<String> roles, Set<String> organizationIds,
                                String requestId) {
        this(userId, roles, organizationIds, requestId, null);
    }

    public CustomerServiceActor(String userId, Set<String> roles, Set<String> organizationIds,
                                String requestId, CustomerServiceConfirmation confirmation) {
        this.userId = userId;
        this.roles = Collections.unmodifiableSet(new HashSet<>(roles));
        this.organizationIds = Collections.unmodifiableSet(new HashSet<>(organizationIds));
        this.requestId = requestId;
        this.confirmation = confirmation;
    }

    public String getUserId() { return userId; }
    public Set<String> getRoles() { return roles; }
    public Set<String> getOrganizationIds() { return organizationIds; }
    public String getRequestId() { return requestId; }
    public CustomerServiceConfirmation getConfirmation() { return confirmation; }
    public boolean isAdministrator() {
        return roles.contains(SYSTEM_ADMIN) || roles.contains(ORGANIZATION_ADMIN);
    }
    public boolean canAccessOrganization(String organizationId) {
        return roles.contains(SYSTEM_ADMIN) || organizationIds.contains(organizationId);
    }
}

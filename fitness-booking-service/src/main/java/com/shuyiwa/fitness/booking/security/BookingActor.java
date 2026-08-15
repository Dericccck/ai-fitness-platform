package com.shuyiwa.fitness.booking.security;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * Gateway 传入的最小业务主体。
 *
 * <p>预约服务不相信模型生成的用户 ID；它只使用 Gateway 根据签名上下文注入的主体、
 * 角色和机构范围，并在 SQL 查询中再次绑定目标资源。</p>
 */
public final class BookingActor {
    public static final String SYSTEM_ADMIN = "SYSTEM_ADMIN";
    public static final String ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN";
    public static final String COACH = "COACH";
    public static final String STUDENT = "STUDENT";

    private final String userId;
    private final Set<String> roles;
    private final Set<String> organizationIds;
    private final String requestId;
    private final BookingConfirmation confirmation;

    public BookingActor(String userId, Set<String> roles, Set<String> organizationIds, String requestId,
                        BookingConfirmation confirmation) {
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
    public BookingConfirmation getConfirmation() { return confirmation; }
    public boolean hasRole(String role) { return roles.contains(role); }
    public boolean isAdministrator() { return hasRole(SYSTEM_ADMIN) || hasRole(ORGANIZATION_ADMIN); }
    public boolean canAccessOrganization(String organizationId) {
        return hasRole(SYSTEM_ADMIN) || organizationIds.contains(organizationId);
    }
}

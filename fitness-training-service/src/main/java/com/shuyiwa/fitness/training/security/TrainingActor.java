package com.shuyiwa.fitness.training.security;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * Gateway 传入的已认证业务主体。
 *
 * <p>训练服务不信任请求体中的 actorId 或 role。生产环境由 Gateway 验证签名上下文后，
 * 通过内部网络和内部 Token 传入这些字段；训练服务仍会再次执行资源级权限检查。</p>
 */
public final class TrainingActor {

    public static final String SYSTEM_ADMIN = "SYSTEM_ADMIN";
    public static final String ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN";
    public static final String COACH = "COACH";
    public static final String STUDENT = "STUDENT";

    private final String userId;
    private final Set<String> roles;
    private final Set<String> organizationIds;
    private final String requestId;

    public TrainingActor(String userId, Set<String> roles, Set<String> organizationIds, String requestId) {
        this.userId = userId;
        this.roles = Collections.unmodifiableSet(new HashSet<>(roles));
        this.organizationIds = Collections.unmodifiableSet(new HashSet<>(organizationIds));
        this.requestId = requestId;
    }

    public String getUserId() {
        return userId;
    }

    public Set<String> getRoles() {
        return roles;
    }

    public Set<String> getOrganizationIds() {
        return organizationIds;
    }

    public boolean canAccessOrganization(String organizationId) {
        return hasRole(SYSTEM_ADMIN) || organizationIds.contains(organizationId);
    }

    public String getRequestId() {
        return requestId;
    }

    public boolean hasRole(String role) {
        return roles.contains(role);
    }

    public boolean isAdministrator() {
        return hasRole(SYSTEM_ADMIN) || hasRole(ORGANIZATION_ADMIN);
    }
}

package com.shuyiwa.fitness.gateway.security;

import java.time.Instant;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * 经认证服务签名的 Agent 请求上下文。
 *
 * <p>Agent 不能自行声明“我是某个学员”或“我属于某个机构”。这些字段来自已签名的
 * 上下文，并在每个 Tool 调用前再次经过资源级策略检查。角色名称使用当前健身项目的
 * 稳定语义，不把赛事运营权限带入新 Gateway。</p>
 */
public final class AgentContext {

    public static final String ROLE_SYSTEM_ADMIN = "SYSTEM_ADMIN";
    public static final String ROLE_ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN";
    public static final String ROLE_COACH = "COACH";
    public static final String ROLE_STUDENT = "STUDENT";

    private final String subjectUserId;
    private final Set<String> organizationIds;
    private final Set<String> roles;
    private final Set<String> capabilities;
    private final Set<String> qualifications;
    private final Instant issuedAt;
    private final Instant expiresAt;
    private final String nonce;

    public AgentContext(
            String subjectUserId,
            Set<String> organizationIds,
            Set<String> roles,
            Instant issuedAt,
            Instant expiresAt,
            String nonce
    ) {
        this(subjectUserId, organizationIds, roles, Collections.emptySet(),
                Collections.emptySet(), issuedAt, expiresAt, nonce);
    }

    /**
     * 完整签名上下文。capabilities 表示被授予的窄范围业务能力，qualifications 表示
     * 已由认证端核验的专业资质；两者都不能从 Agent 请求体或前端表单中补写。
     */
    public AgentContext(
            String subjectUserId,
            Set<String> organizationIds,
            Set<String> roles,
            Set<String> capabilities,
            Set<String> qualifications,
            Instant issuedAt,
            Instant expiresAt,
            String nonce
    ) {
        this.subjectUserId = subjectUserId;
        this.organizationIds = immutableCopy(organizationIds);
        this.roles = immutableCopy(roles);
        this.capabilities = immutableCopy(capabilities);
        this.qualifications = immutableCopy(qualifications);
        this.issuedAt = issuedAt;
        this.expiresAt = expiresAt;
        this.nonce = nonce;
    }

    private static Set<String> immutableCopy(Set<String> values) {
        return Collections.unmodifiableSet(new HashSet<>(values));
    }

    public String getSubjectUserId() {
        return subjectUserId;
    }

    public Set<String> getOrganizationIds() {
        return organizationIds;
    }

    public Set<String> getRoles() {
        return roles;
    }

    public Set<String> getCapabilities() {
        return capabilities;
    }

    public Set<String> getQualifications() {
        return qualifications;
    }

    public Instant getIssuedAt() {
        return issuedAt;
    }

    public Instant getExpiresAt() {
        return expiresAt;
    }

    public String getNonce() {
        return nonce;
    }

    public boolean hasRole(String role) {
        return roles.contains(role);
    }

    public boolean isSystemAdmin() {
        return hasRole(ROLE_SYSTEM_ADMIN);
    }

    public boolean isOrganizationAdmin() {
        return hasRole(ROLE_ORGANIZATION_ADMIN);
    }

    public boolean canReadAnyUserInOrganization() {
        return isSystemAdmin() || isOrganizationAdmin();
    }

    public boolean canAccessOrganization(String organizationId) {
        return isSystemAdmin() || organizationIds.contains(organizationId);
    }
}

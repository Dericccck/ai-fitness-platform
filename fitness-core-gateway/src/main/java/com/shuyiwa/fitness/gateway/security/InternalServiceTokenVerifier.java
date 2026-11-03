package com.shuyiwa.fitness.gateway.security;

import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import org.springframework.stereotype.Component;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Map;

/** Credentials identify a caller and permitted purpose; identity never comes from caller-supplied headers. */
@Component
public class InternalServiceTokenVerifier {
    public static final String EXECUTE = "TOOLS_EXECUTE";
    public static final String QUERY = "INTERNAL_QUERY";
    public static final String RECONCILE = "RECONCILE";
    private final GatewayProperties properties;

    public InternalServiceTokenVerifier(GatewayProperties properties) { this.properties = properties; }

    public String verify(String token, String... allowedScopes) {
        if (token == null || token.isEmpty() || token.length() > 4096) {
            throw new GatewaySecurityException("内部服务凭证无效");
        }
        String digest = sha256(token);
        String principal = null;
        GatewayProperties.ServiceCredential credential = null;
        for (Map.Entry<String, GatewayProperties.ServiceCredential> entry : properties.getServiceCredentials().entrySet()) {
            GatewayProperties.ServiceCredential candidate = entry.getValue();
            if (candidate != null && candidate.getTokenSha256() != null
                    && constantEquals(digest, candidate.getTokenSha256().toLowerCase(java.util.Locale.ROOT))) {
                if (principal != null) throw new GatewaySecurityException("服务凭证配置不唯一");
                principal = entry.getKey();
                credential = candidate;
            }
        }
        long now = Instant.now().getEpochSecond();
        if (credential != null) {
            if (principal.trim().isEmpty() || credential.getExpiresAtEpochSeconds() <= now) {
                throw new GatewaySecurityException("内部服务凭证已过期");
            }
            for (String scope : allowedScopes) if (credential.getScopes().contains(scope)) return principal;
            throw new GatewayForbiddenException("服务身份无权访问此路由");
        }
        // Disabled by default; a rollout must supply an explicit expiration.
        if (properties.isAllowLegacyInternalServiceToken()
                && properties.getLegacyInternalServiceTokenExpiresAt() > now
                && properties.getInternalServiceToken() != null && !properties.getInternalServiceToken().isEmpty()
                && constantEquals(token, properties.getInternalServiceToken())) return "legacy-transition";
        throw new GatewaySecurityException("内部服务凭证无效");
    }

    public String verifyToolRequest(String token, String method, String path) {
        if (!path.startsWith("/internal/agent-tools/v1/") || path.contains(";") || path.contains("%")) {
            throw new GatewayForbiddenException("服务路由不受支持");
        }
        String route = path.substring("/internal/agent-tools/v1/".length());
        if ("GET".equals(method)) {
            if (route.equals("training-context-access")
                    || route.matches("booking/operations/[^/]+")
                    || (route.matches("operations/[^/]+") && !route.equals("operations/metrics"))) {
                return verify(token, QUERY);
            }
            if (route.matches("training/plans/[^/]+")) return verify(token, QUERY, EXECUTE);
            if (route.equals("me") || route.matches("organizations/[^/]+")
                    || route.equals("courses") || route.equals("contracts") || route.equals("appointments")
                    || route.equals("booking/availability") || route.equals("operations/metrics")
                    || route.matches("training/plans/[^/]+/executions")
                    || route.matches("customer-service/tickets(?:/[^/]+)?")) return verify(token, EXECUTE);
        } else if ("POST".equals(method)) {
            if (route.equals("appointments") || route.matches("appointments/[^/]+/(?:reschedule|cancel)")
                    || route.equals("training/plans/drafts")
                    || route.matches("training/plans/[^/]+/(?:submit-review|review|publish)")
                    || route.matches("training/plans/[^/]+/days/[^/]+/execution")
                    || route.equals("customer-service/tickets")) return verify(token, EXECUTE);
        }
        throw new GatewayForbiddenException("服务路由不受支持");
    }

    public static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(64);
            for (byte b : digest) result.append(String.format("%02x", b));
            return result.toString();
        } catch (java.security.NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
    }
    private static boolean constantEquals(String a, String b) {
        return MessageDigest.isEqual(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8));
    }
}

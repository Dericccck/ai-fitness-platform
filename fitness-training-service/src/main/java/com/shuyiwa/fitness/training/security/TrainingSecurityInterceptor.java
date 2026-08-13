package com.shuyiwa.fitness.training.security;

import com.shuyiwa.fitness.training.config.TrainingProperties;
import org.springframework.web.servlet.HandlerInterceptor;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.util.Arrays;
import java.util.Collections;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 训练服务的内部入口保护。
 *
 * <p>第一层是 Gateway 到训练服务的内部 Token；第二层是 Gateway 已解析的主体信息。
 * 该服务不开放公网，且不允许缺少主体、角色或请求 ID 的写请求进入业务层。</p>
 */
public class TrainingSecurityInterceptor implements HandlerInterceptor {

    public static final String ACTOR_ATTRIBUTE = TrainingSecurityInterceptor.class.getName() + ".actor";
    private final TrainingProperties properties;

    public TrainingSecurityInterceptor(TrainingProperties properties) {
        this.properties = properties;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String expected = trim(properties.getInternalServiceToken());
        String actual = trim(request.getHeader("X-Internal-Service-Token"));
        if (expected.isEmpty() || !expected.equals(actual)) {
            throw new TrainingSecurityException("内部服务认证失败");
        }

        String actorId = trim(request.getHeader("X-Actor-User-Id"));
        String rolesHeader = trim(request.getHeader("X-Actor-Roles"));
        String organizationHeader = trim(request.getHeader("X-Actor-Organization-Ids"));
        String requestId = trim(request.getHeader("X-Request-ID"));
        if (actorId.isEmpty() || rolesHeader.isEmpty() || organizationHeader.isEmpty() || requestId.isEmpty()) {
            throw new TrainingSecurityException("缺少业务主体或请求标识");
        }

        Set<String> roles = Arrays.stream(rolesHeader.split(","))
                .map(String::trim)
                .filter(value -> !value.isEmpty())
                .collect(Collectors.toSet());
        if (roles.isEmpty()) {
            throw new TrainingSecurityException("业务主体没有有效角色");
        }
        Set<String> organizationIds = Arrays.stream(organizationHeader.split(","))
                .map(String::trim)
                .filter(value -> !value.isEmpty())
                .collect(Collectors.toSet());
        if (organizationIds.isEmpty()) {
            throw new TrainingSecurityException("业务主体没有有效机构范围");
        }
        TrainingConfirmation confirmation = parseConfirmation(request);
        request.setAttribute(ACTOR_ATTRIBUTE,
                new TrainingActor(actorId, roles, organizationIds, requestId, confirmation));
        return true;
    }

    private TrainingConfirmation parseConfirmation(HttpServletRequest request) {
        String confirmationId = trim(request.getHeader("X-Confirmation-Id"));
        String jti = trim(request.getHeader("X-Confirmation-JTI"));
        String toolId = trim(request.getHeader("X-Confirmation-Tool-ID"));
        String action = trim(request.getHeader("X-Confirmation-Action"));
        String organizationId = trim(request.getHeader("X-Confirmation-Organization-ID"));
        String resource = trim(request.getHeader("X-Confirmation-Resource"));
        String payloadHash = trim(request.getHeader("X-Confirmation-Payload-Hash"));
        boolean anyPresent = !confirmationId.isEmpty() || !jti.isEmpty() || !toolId.isEmpty()
                || !action.isEmpty() || !organizationId.isEmpty() || !resource.isEmpty()
                || !payloadHash.isEmpty();
        boolean allPresent = !confirmationId.isEmpty() && !jti.isEmpty() && !toolId.isEmpty()
                && !action.isEmpty() && !organizationId.isEmpty() && !resource.isEmpty()
                && payloadHash.matches("[0-9a-fA-F]{64}");
        if (anyPresent && !allPresent) {
            throw new TrainingSecurityException("确认声明不完整");
        }
        return allPresent ? new TrainingConfirmation(
                confirmationId, jti, toolId, action, organizationId, resource, payloadHash
        ) : null;
    }

    private static String trim(String value) {
        return value == null ? "" : value.trim();
    }
}

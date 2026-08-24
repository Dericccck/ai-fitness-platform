package com.shuyiwa.fitness.customer.security;

import com.shuyiwa.fitness.customer.config.CustomerServiceProperties;
import org.springframework.web.servlet.HandlerInterceptor;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.util.Arrays;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 客服内部 API 的双层调用边界中的第二层。
 *
 * <p>Gateway 负责验证签名 AgentContext；客服服务再验证服务间 Token、主体角色、机构
 * 范围和请求 ID，避免单个服务被绕过后直接查询全量工单。</p>
 */
public class CustomerServiceSecurityInterceptor implements HandlerInterceptor {

    public static final String ACTOR_ATTRIBUTE = CustomerServiceSecurityInterceptor.class.getName() + ".actor";
    private final CustomerServiceProperties properties;

    public CustomerServiceSecurityInterceptor(CustomerServiceProperties properties) {
        this.properties = properties;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String expected = trim(properties.getInternalServiceToken());
        String actual = trim(request.getHeader("X-Internal-Service-Token"));
        if (expected.isEmpty() || !expected.equals(actual)) {
            throw new CustomerServiceSecurityException("内部服务认证失败");
        }
        String userId = trim(request.getHeader("X-Actor-User-Id"));
        Set<String> roles = split(request.getHeader("X-Actor-Roles"));
        Set<String> organizationIds = split(request.getHeader("X-Actor-Organization-Ids"));
        String requestId = trim(request.getHeader("X-Request-ID"));
        if (userId.isEmpty() || roles.isEmpty() || organizationIds.isEmpty() || requestId.isEmpty()) {
            throw new CustomerServiceSecurityException("缺少业务主体或请求标识");
        }
        request.setAttribute(ACTOR_ATTRIBUTE,
                new CustomerServiceActor(userId, roles, organizationIds, requestId));
        return true;
    }

    private static Set<String> split(String value) {
        return Arrays.stream(trim(value).split(","))
                .map(String::trim).filter(item -> !item.isEmpty()).collect(Collectors.toSet());
    }

    private static String trim(String value) {
        return value == null ? "" : value.trim();
    }
}

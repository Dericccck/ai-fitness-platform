package com.shuyiwa.fitness.booking.security;

import com.shuyiwa.fitness.booking.config.BookingProperties;
import org.springframework.web.servlet.HandlerInterceptor;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.util.Arrays;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 预约写服务的内部入口保护。
 *
 * <p>Gateway 会先验证外部确认 Token，这里不接收原始 Token，只接收已验签声明；
 * 业务事务还会用 JTI 唯一键完成一次性消费。</p>
 */
public class BookingSecurityInterceptor implements HandlerInterceptor {
    public static final String ACTOR_ATTRIBUTE = BookingSecurityInterceptor.class.getName() + ".actor";
    private final BookingProperties properties;

    public BookingSecurityInterceptor(BookingProperties properties) { this.properties = properties; }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        if (!trim(properties.getInternalServiceToken()).equals(trim(request.getHeader("X-Internal-Service-Token")))
                || trim(properties.getInternalServiceToken()).isEmpty()) {
            throw new BookingSecurityException("内部服务认证失败");
        }
        String userId = trim(request.getHeader("X-Actor-User-Id"));
        Set<String> roles = values(request.getHeader("X-Actor-Roles"));
        Set<String> organizations = values(request.getHeader("X-Actor-Organization-Ids"));
        String requestId = trim(request.getHeader("X-Request-ID"));
        if (userId.isEmpty() || roles.isEmpty() || organizations.isEmpty() || requestId.isEmpty()) {
            throw new BookingSecurityException("缺少业务主体或请求标识");
        }
        request.setAttribute(ACTOR_ATTRIBUTE, new BookingActor(
                userId, roles, organizations, requestId, parseConfirmation(request)
        ));
        return true;
    }

    private BookingConfirmation parseConfirmation(HttpServletRequest request) {
        String confirmationId = trim(request.getHeader("X-Confirmation-Id"));
        String jti = trim(request.getHeader("X-Confirmation-JTI"));
        String toolId = trim(request.getHeader("X-Confirmation-Tool-ID"));
        String action = trim(request.getHeader("X-Confirmation-Action"));
        String organizationId = trim(request.getHeader("X-Confirmation-Organization-ID"));
        String resource = trim(request.getHeader("X-Confirmation-Resource"));
        String payloadHash = trim(request.getHeader("X-Confirmation-Payload-Hash"));
        boolean any = !confirmationId.isEmpty() || !jti.isEmpty() || !toolId.isEmpty() || !action.isEmpty()
                || !organizationId.isEmpty() || !resource.isEmpty() || !payloadHash.isEmpty();
        boolean complete = !confirmationId.isEmpty() && !jti.isEmpty() && !toolId.isEmpty()
                && !action.isEmpty() && !organizationId.isEmpty() && !resource.isEmpty()
                && payloadHash.matches("[0-9a-fA-F]{64}");
        if (any && !complete) {
            throw new BookingSecurityException("确认声明不完整");
        }
        return complete ? new BookingConfirmation(
                confirmationId, jti, toolId, action, organizationId, resource, payloadHash
        ) : null;
    }

    private static Set<String> values(String raw) {
        return Arrays.stream(trim(raw).split(","))
                .map(String::trim).filter(value -> !value.isEmpty()).collect(Collectors.toSet());
    }

    private static String trim(String value) { return value == null ? "" : value.trim(); }
}

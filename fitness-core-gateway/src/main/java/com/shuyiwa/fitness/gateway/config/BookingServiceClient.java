package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.api.BookingToolInputs;
import com.shuyiwa.fitness.gateway.api.ToolViews;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.ConfirmationTokenClaims;
import com.shuyiwa.fitness.gateway.security.ConfirmationTokenVerifier;
import com.shuyiwa.fitness.gateway.security.GatewayConflictException;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import com.shuyiwa.fitness.gateway.security.GatewayResourceNotFoundException;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

/**
 * Gateway 到预约写服务的固定客户端。
 *
 * <p>Gateway 在这里验证确认凭证的工具、动作、机构、合同资源和请求 ID，然后只把
 * 已验签的声明转发给业务服务；业务服务仍会在事务内消费 JTI。</p>
 */
@Component
public class BookingServiceClient {
    private final RestTemplate restTemplate;
    private final BookingServiceProperties properties;
    private final ConfirmationTokenVerifier tokenVerifier;

    public BookingServiceClient(RestTemplate bookingServiceRestTemplate, BookingServiceProperties properties,
                                ConfirmationTokenVerifier tokenVerifier) {
        this.restTemplate = bookingServiceRestTemplate;
        this.properties = properties;
        this.tokenVerifier = tokenVerifier;
    }

    public ToolViews.BookingCreatedView create(AgentContext context, String requestId, String confirmationToken,
                                               BookingToolInputs.CreateInput input) {
        if (properties.getInternalServiceToken() == null
                || properties.getInternalServiceToken().trim().isEmpty()) {
            // 预约写服务必须通过内部凭证调用；配置缺失时直接失败，不能退化成无认证内部请求。
            throw new IllegalStateException("预约服务内部 Token 未配置");
        }
        ConfirmationTokenClaims claims = tokenVerifier.verify(
                confirmationToken, context, "fitness.booking.create.v1", "CREATE_APPOINTMENT",
                input.getContractId(), requestId);
        if (!input.getOrganizationId().equals(claims.getOrganizationId())) {
            throw new GatewayForbiddenException("确认凭证中的机构与请求不匹配");
        }
        if (!context.canAccessOrganization(input.getOrganizationId())) {
            throw new GatewayForbiddenException("机构不在授权范围内");
        }
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Internal-Service-Token", properties.getInternalServiceToken());
        headers.set("X-Actor-User-Id", context.getSubjectUserId());
        headers.set("X-Actor-Roles", String.join(",", context.getRoles()));
        headers.set("X-Actor-Organization-Ids", String.join(",", context.getOrganizationIds()));
        headers.set("X-Request-ID", requestId);
        headers.set("X-Confirmation-Id", claims.getConfirmationId());
        headers.set("X-Confirmation-JTI", claims.getJti());
        headers.set("X-Confirmation-Tool-ID", claims.getToolId());
        headers.set("X-Confirmation-Action", claims.getAction());
        headers.set("X-Confirmation-Organization-ID", claims.getOrganizationId());
        headers.set("X-Confirmation-Resource", claims.getResource());
        headers.set("X-Confirmation-Payload-Hash", claims.getPayloadHash());
        try {
            ResponseEntity<BookingServiceViews.Appointment> response = restTemplate.exchange(
                    properties.getBaseUrl().replaceAll("/$", "") + "/internal/booking/v1/appointments",
                    HttpMethod.POST, new HttpEntity<>(input, headers), BookingServiceViews.Appointment.class);
            if (response.getBody() == null) throw new IllegalStateException("预约服务返回空响应");
            return response.getBody().toToolView();
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 401) throw new GatewayForbiddenException("需要预约确认凭证");
            if (exception.getStatusCode().value() == 403) throw new GatewayForbiddenException("预约资源不在授权范围内");
            if (exception.getStatusCode().value() == 404) throw new GatewayResourceNotFoundException("预约资源不存在");
            if (exception.getStatusCode().value() == 409) throw new GatewayConflictException("预约与当前业务事实冲突");
            throw new IllegalArgumentException("预约服务拒绝了请求");
        } catch (RestClientException exception) {
            throw new IllegalStateException("预约服务暂时不可用", exception);
        }
    }

    public ToolViews.BookingOperationView queryOperation(AgentContext context, String operationId) {
        if (properties.getInternalServiceToken() == null
                || properties.getInternalServiceToken().trim().isEmpty()) {
            throw new IllegalStateException("预约服务内部 Token 未配置");
        }
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Internal-Service-Token", properties.getInternalServiceToken());
        headers.set("X-Actor-User-Id", context.getSubjectUserId());
        headers.set("X-Actor-Roles", String.join(",", context.getRoles()));
        headers.set("X-Actor-Organization-Ids", String.join(",", context.getOrganizationIds()));
        try {
            ResponseEntity<BookingServiceViews.Operation> response = restTemplate.exchange(
                    properties.getBaseUrl().replaceAll("/$", "") + "/internal/booking/v1/operations/" + operationId,
                    HttpMethod.GET, new HttpEntity<>(headers), BookingServiceViews.Operation.class);
            BookingServiceViews.Operation body = response.getBody();
            if (body == null) throw new IllegalStateException("预约服务返回空响应");
            ToolViews.BookingCreatedView appointment = body.appointment == null ? null : body.appointment.toToolView();
            return new ToolViews.BookingOperationView(body.operationId, body.status, appointment);
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 403) throw new GatewayForbiddenException("操作不在授权范围内");
            throw new GatewayResourceNotFoundException("未找到预约操作");
        } catch (RestClientException exception) {
            throw new IllegalStateException("预约服务暂时不可用", exception);
        }
    }

    public ToolViews.BookingCreatedView reschedule(AgentContext context, String requestId, String confirmationToken,
                                                   BookingToolInputs.RescheduleInput input) {
        if (properties.getInternalServiceToken() == null
                || properties.getInternalServiceToken().trim().isEmpty()) {
            throw new IllegalStateException("预约服务内部 Token 未配置");
        }
        ConfirmationTokenClaims claims = tokenVerifier.verify(
                confirmationToken, context, "fitness.booking.reschedule.v1", "RESCHEDULE_APPOINTMENT",
                input.getAppointmentId(), requestId);
        if (!input.getOrganizationId().equals(claims.getOrganizationId())
                || !context.canAccessOrganization(input.getOrganizationId())) {
            throw new GatewayForbiddenException("改约请求不在授权范围内");
        }
        HttpHeaders headers = bookingHeaders(context, requestId, claims);
        try {
            ResponseEntity<BookingServiceViews.Appointment> response = restTemplate.exchange(
                    properties.getBaseUrl().replaceAll("/$", "") + "/internal/booking/v1/appointments/"
                            + input.getAppointmentId() + "/reschedule",
                    HttpMethod.POST, new HttpEntity<>(input, headers), BookingServiceViews.Appointment.class);
            if (response.getBody() == null) throw new IllegalStateException("预约服务返回空响应");
            return response.getBody().toToolView();
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 401) throw new GatewayForbiddenException("需要预约确认凭证");
            if (exception.getStatusCode().value() == 403) throw new GatewayForbiddenException("预约资源不在授权范围内");
            if (exception.getStatusCode().value() == 404) throw new GatewayResourceNotFoundException("预约资源不存在");
            if (exception.getStatusCode().value() == 409) throw new GatewayConflictException("预约与当前业务事实冲突");
            throw new IllegalArgumentException("预约服务拒绝了请求");
        } catch (RestClientException exception) {
            throw new IllegalStateException("预约服务暂时不可用", exception);
        }
    }

    public ToolViews.BookingCancelledView cancel(AgentContext context, String requestId, String confirmationToken,
                                                BookingToolInputs.CancelInput input) {
        if (properties.getInternalServiceToken() == null
                || properties.getInternalServiceToken().trim().isEmpty()) {
            throw new IllegalStateException("预约服务内部 Token 未配置");
        }
        ConfirmationTokenClaims claims = tokenVerifier.verify(
                confirmationToken, context, "fitness.booking.cancel.v1", "CANCEL_APPOINTMENT",
                input.getAppointmentId(), requestId);
        if (!input.getOrganizationId().equals(claims.getOrganizationId())
                || !context.canAccessOrganization(input.getOrganizationId())) {
            throw new GatewayForbiddenException("取消预约请求不在授权范围内");
        }
        HttpHeaders headers = bookingHeaders(context, requestId, claims);
        try {
            ResponseEntity<BookingServiceViews.CancelledAppointment> response = restTemplate.exchange(
                    properties.getBaseUrl().replaceAll("/$", "") + "/internal/booking/v1/appointments/"
                            + input.getAppointmentId() + "/cancel",
                    HttpMethod.POST, new HttpEntity<>(input, headers), BookingServiceViews.CancelledAppointment.class);
            if (response.getBody() == null) throw new IllegalStateException("预约服务返回空响应");
            return response.getBody().toToolView();
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 401) throw new GatewayForbiddenException("需要预约确认凭证");
            if (exception.getStatusCode().value() == 403) throw new GatewayForbiddenException("预约资源不在授权范围内");
            if (exception.getStatusCode().value() == 404) throw new GatewayResourceNotFoundException("预约资源不存在");
            if (exception.getStatusCode().value() == 409) throw new GatewayConflictException("当前业务事实不允许取消预约");
            throw new IllegalArgumentException("预约服务拒绝了请求");
        } catch (RestClientException exception) {
            throw new IllegalStateException("预约服务暂时不可用", exception);
        }
    }

    private HttpHeaders bookingHeaders(AgentContext context, String requestId, ConfirmationTokenClaims claims) {
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Internal-Service-Token", properties.getInternalServiceToken());
        headers.set("X-Actor-User-Id", context.getSubjectUserId());
        headers.set("X-Actor-Roles", String.join(",", context.getRoles()));
        headers.set("X-Actor-Organization-Ids", String.join(",", context.getOrganizationIds()));
        headers.set("X-Request-ID", requestId);
        headers.set("X-Confirmation-Id", claims.getConfirmationId());
        headers.set("X-Confirmation-JTI", claims.getJti());
        headers.set("X-Confirmation-Tool-ID", claims.getToolId());
        headers.set("X-Confirmation-Action", claims.getAction());
        headers.set("X-Confirmation-Organization-ID", claims.getOrganizationId());
        headers.set("X-Confirmation-Resource", claims.getResource());
        headers.set("X-Confirmation-Payload-Hash", claims.getPayloadHash());
        return headers;
    }
}

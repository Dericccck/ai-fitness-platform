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
            throw new IllegalStateException("booking service internal token is not configured");
        }
        ConfirmationTokenClaims claims = tokenVerifier.verify(
                confirmationToken, context, "fitness.booking.create.v1", "CREATE_APPOINTMENT",
                input.getContractId(), requestId);
        if (!input.getOrganizationId().equals(claims.getOrganizationId())) {
            throw new GatewayForbiddenException("confirmation organization does not match request");
        }
        if (!context.canAccessOrganization(input.getOrganizationId())) {
            throw new GatewayForbiddenException("organization is outside the authorized scope");
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
            if (response.getBody() == null) throw new IllegalStateException("booking service returned empty response");
            return response.getBody().toToolView();
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 401) throw new GatewayForbiddenException("booking confirmation required");
            if (exception.getStatusCode().value() == 403) throw new GatewayForbiddenException("booking resource is outside scope");
            if (exception.getStatusCode().value() == 404) throw new GatewayResourceNotFoundException("booking resource not found");
            if (exception.getStatusCode().value() == 409) throw new GatewayConflictException("booking conflicts with current business facts");
            throw new IllegalArgumentException("booking service rejected the request");
        } catch (RestClientException exception) {
            throw new IllegalStateException("booking service is temporarily unavailable", exception);
        }
    }

    public ToolViews.BookingCreatedView reschedule(AgentContext context, String requestId, String confirmationToken,
                                                   BookingToolInputs.RescheduleInput input) {
        if (properties.getInternalServiceToken() == null
                || properties.getInternalServiceToken().trim().isEmpty()) {
            throw new IllegalStateException("booking service internal token is not configured");
        }
        ConfirmationTokenClaims claims = tokenVerifier.verify(
                confirmationToken, context, "fitness.booking.reschedule.v1", "RESCHEDULE_APPOINTMENT",
                input.getAppointmentId(), requestId);
        if (!input.getOrganizationId().equals(claims.getOrganizationId())
                || !context.canAccessOrganization(input.getOrganizationId())) {
            throw new GatewayForbiddenException("reschedule request is outside the authorized scope");
        }
        HttpHeaders headers = bookingHeaders(context, requestId, claims);
        try {
            ResponseEntity<BookingServiceViews.Appointment> response = restTemplate.exchange(
                    properties.getBaseUrl().replaceAll("/$", "") + "/internal/booking/v1/appointments/"
                            + input.getAppointmentId() + "/reschedule",
                    HttpMethod.POST, new HttpEntity<>(input, headers), BookingServiceViews.Appointment.class);
            if (response.getBody() == null) throw new IllegalStateException("booking service returned empty response");
            return response.getBody().toToolView();
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 401) throw new GatewayForbiddenException("booking confirmation required");
            if (exception.getStatusCode().value() == 403) throw new GatewayForbiddenException("booking resource is outside scope");
            if (exception.getStatusCode().value() == 404) throw new GatewayResourceNotFoundException("booking resource not found");
            if (exception.getStatusCode().value() == 409) throw new GatewayConflictException("booking conflicts with current business facts");
            throw new IllegalArgumentException("booking service rejected the request");
        } catch (RestClientException exception) {
            throw new IllegalStateException("booking service is temporarily unavailable", exception);
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

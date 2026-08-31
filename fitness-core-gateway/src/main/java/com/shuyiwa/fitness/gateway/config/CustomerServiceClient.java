package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import com.shuyiwa.fitness.gateway.api.CustomerServiceToolInputs;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.ConfirmationTokenClaims;
import com.shuyiwa.fitness.gateway.security.ConfirmationTokenVerifier;
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
import org.springframework.web.util.UriComponentsBuilder;

import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;

/**
 * Gateway 到客服工单服务的固定客户端。
 *
 * <p>客服服务的所有查询都带上 Gateway 已验证的用户、角色、机构和请求 ID；Agent
 * 不能通过查询参数伪造主体，也不能绕过 Gateway 直接访问客服数据库。</p>
 */
@Component
public class CustomerServiceClient {

    private final RestTemplate restTemplate;
    private final CustomerServiceProperties properties;
    private final ConfirmationTokenVerifier confirmationTokenVerifier;

    public CustomerServiceClient(RestTemplate customerServiceRestTemplate,
                                 CustomerServiceProperties properties,
                                 ConfirmationTokenVerifier confirmationTokenVerifier) {
        this.restTemplate = customerServiceRestTemplate;
        this.properties = properties;
        this.confirmationTokenVerifier = confirmationTokenVerifier;
    }

    public List<ToolViews.CustomerServiceTicketView> list(AgentContext context, String requestId,
                                                          String organizationId, String subjectUserId,
                                                          String status, Integer limit) {
        requireOrganization(context, organizationId);
        UriComponentsBuilder builder = UriComponentsBuilder
                .fromHttpUrl(properties.getBaseUrl().replaceAll("/$", "")
                        + "/internal/customer-service/v1/tickets")
                .queryParam("organizationId", organizationId);
        if (subjectUserId != null) builder.queryParam("subjectUserId", subjectUserId);
        if (status != null) builder.queryParam("status", status);
        if (limit != null) builder.queryParam("limit", limit);
        CustomerServiceViews.Ticket[] response = exchange(builder.toUriString(), context, requestId,
                CustomerServiceViews.Ticket[].class);
        return Arrays.stream(response).map(CustomerServiceViews.Ticket::toToolView)
                .collect(Collectors.toList());
    }

    public ToolViews.CustomerServiceTicketView get(AgentContext context, String requestId,
                                                   String organizationId, String ticketId) {
        requireOrganization(context, organizationId);
        String url = UriComponentsBuilder
                .fromHttpUrl(properties.getBaseUrl().replaceAll("/$", "")
                        + "/internal/customer-service/v1/tickets/" + ticketId)
                .queryParam("organizationId", organizationId).toUriString();
        return exchange(url, context, requestId, CustomerServiceViews.Ticket.class).toToolView();
    }

    public ToolViews.CustomerServiceTicketView create(AgentContext context, String requestId,
                                                      String confirmationToken,
                                                      CustomerServiceToolInputs.CreateInput input) {
        String subjectUserId = input.getSubjectUserId() == null
                ? context.getSubjectUserId() : input.getSubjectUserId();
        String resource = input.getOrganizationId() + ":" + subjectUserId;
        ConfirmationTokenClaims claims = confirmationTokenVerifier.verify(
                confirmationToken, context, "fitness.support.ticket.create.v1",
                "CREATE_CUSTOMER_SERVICE_TICKET", resource, requestId);
        if (!input.getOrganizationId().equals(claims.getOrganizationId())) {
            throw new GatewayForbiddenException("确认凭证中的机构与请求不匹配");
        }
        requireOrganization(context, input.getOrganizationId());
        return exchangePost(properties.getBaseUrl().replaceAll("/$", "")
                        + "/internal/customer-service/v1/tickets", context, requestId, claims, input,
                CustomerServiceViews.Ticket.class).toToolView();
    }

    private <T> T exchange(String url, AgentContext context, String requestId, Class<T> responseType) {
        if (properties.getInternalServiceToken().trim().isEmpty()) {
            throw new IllegalStateException("客服服务内部 Token 未配置");
        }
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Internal-Service-Token", properties.getInternalServiceToken());
        headers.set("X-Actor-User-Id", context.getSubjectUserId());
        headers.set("X-Actor-Roles", String.join(",", context.getRoles()));
        headers.set("X-Actor-Organization-Ids", String.join(",", context.getOrganizationIds()));
        headers.set("X-Request-ID", requestId);
        try {
            ResponseEntity<T> response = restTemplate.exchange(
                    url, HttpMethod.GET, new HttpEntity<>(headers), responseType);
            if (response.getBody() == null) {
                throw new IllegalStateException("客服服务返回空响应");
            }
            return response.getBody();
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 403) {
                throw new GatewayForbiddenException("客服资源不在授权范围内");
            }
            if (exception.getStatusCode().value() == 404) {
                throw new GatewayResourceNotFoundException("客服工单不存在");
            }
            throw new IllegalArgumentException("客服服务拒绝了请求");
        } catch (RestClientException exception) {
            throw new IllegalStateException("客服服务暂时不可用", exception);
        }
    }

    private <T> T exchangePost(String url, AgentContext context, String requestId,
                               ConfirmationTokenClaims claims, Object body, Class<T> responseType) {
        if (properties.getInternalServiceToken().trim().isEmpty()) {
            throw new IllegalStateException("客服服务内部 Token 未配置");
        }
        HttpHeaders headers = baseHeaders(context, requestId);
        headers.set("X-Confirmation-Id", claims.getConfirmationId());
        headers.set("X-Confirmation-JTI", claims.getJti());
        headers.set("X-Confirmation-Tool-ID", claims.getToolId());
        headers.set("X-Confirmation-Action", claims.getAction());
        headers.set("X-Confirmation-Organization-ID", claims.getOrganizationId());
        headers.set("X-Confirmation-Resource", claims.getResource());
        headers.set("X-Confirmation-Payload-Hash", claims.getPayloadHash());
        try {
            ResponseEntity<T> response = restTemplate.exchange(
                    url, HttpMethod.POST, new HttpEntity<>(body, headers), responseType);
            if (response.getBody() == null) {
                throw new IllegalStateException("客服服务返回空响应");
            }
            return response.getBody();
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 403) {
                throw new GatewayForbiddenException("客服资源不在授权范围内");
            }
            if (exception.getStatusCode().value() == 404) {
                throw new GatewayResourceNotFoundException("客服工单不存在");
            }
            if (exception.getStatusCode().value() == 409) {
                throw new com.shuyiwa.fitness.gateway.security.GatewayConflictException(
                        "客服工单请求与现有请求冲突");
            }
            throw new IllegalArgumentException("客服服务拒绝了请求");
        } catch (RestClientException exception) {
            throw new IllegalStateException("客服服务暂时不可用", exception);
        }
    }

    private HttpHeaders baseHeaders(AgentContext context, String requestId) {
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Internal-Service-Token", properties.getInternalServiceToken());
        headers.set("X-Actor-User-Id", context.getSubjectUserId());
        headers.set("X-Actor-Roles", String.join(",", context.getRoles()));
        headers.set("X-Actor-Organization-Ids", String.join(",", context.getOrganizationIds()));
        headers.set("X-Request-ID", requestId);
        return headers;
    }

    private void requireOrganization(AgentContext context, String organizationId) {
        if (organizationId == null || !context.canAccessOrganization(organizationId)) {
            throw new GatewayForbiddenException("机构不在授权范围内");
        }
    }
}

package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import com.shuyiwa.fitness.gateway.security.AgentContext;
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
 * Gateway 到客服工单服务的固定只读客户端。
 *
 * <p>客服服务的所有查询都带上 Gateway 已验证的用户、角色、机构和请求 ID；Agent
 * 不能通过查询参数伪造主体，也不能绕过 Gateway 直接访问客服数据库。</p>
 */
@Component
public class CustomerServiceClient {

    private final RestTemplate restTemplate;
    private final CustomerServiceProperties properties;

    public CustomerServiceClient(RestTemplate customerServiceRestTemplate,
                                 CustomerServiceProperties properties) {
        this.restTemplate = customerServiceRestTemplate;
        this.properties = properties;
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

    private <T> T exchange(String url, AgentContext context, String requestId, Class<T> responseType) {
        if (properties.getInternalServiceToken().trim().isEmpty()) {
            throw new IllegalStateException("customer service internal token is not configured");
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
                throw new IllegalStateException("customer service returned an empty response");
            }
            return response.getBody();
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 403) {
                throw new GatewayForbiddenException("customer service resource is outside the authorized scope");
            }
            if (exception.getStatusCode().value() == 404) {
                throw new GatewayResourceNotFoundException("customer service ticket was not found");
            }
            throw new IllegalArgumentException("customer service rejected the request");
        } catch (RestClientException exception) {
            throw new IllegalStateException("customer service is temporarily unavailable", exception);
        }
    }

    private void requireOrganization(AgentContext context, String organizationId) {
        if (organizationId == null || !context.canAccessOrganization(organizationId)) {
            throw new GatewayForbiddenException("organization is outside the authorized scope");
        }
    }
}

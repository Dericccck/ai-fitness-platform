package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.ConfirmationTokenVerifier;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import com.shuyiwa.fitness.gateway.security.GatewayResourceNotFoundException;
import org.junit.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.HttpServerErrorException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestTemplate;
import org.junit.Before;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Collections;
import java.util.HashSet;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.fail;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

/**
 * Gateway 客服客户端的边界测试。
 *
 * <p>这里测试的不是客服服务本身，而是 Gateway 对下游结果的统一翻译：Agent 只能
 * 看到稳定的权限拒绝、资源不存在或服务暂不可用语义，不能把下游 HTTP 细节误当成
 * 成功结果。请求头测试同时保证真实的 AgentContext 会沿内部调用链传递，避免只测
 * 返回值却漏掉租户和主体边界。</p>
 */
public class CustomerServiceClientTest {

    private final RestTemplate restTemplate = mock(RestTemplate.class);
    private final CustomerServiceProperties properties = new CustomerServiceProperties();
    private final ConfirmationTokenVerifier confirmationTokenVerifier = mock(ConfirmationTokenVerifier.class);
    private final CustomerServiceClient client = new CustomerServiceClient(
            restTemplate, properties, confirmationTokenVerifier
    );

    @Before
    public void setUp() {
        properties.setInternalServiceToken("customer-service-internal-token");
    }

    @Test
    public void listMapsTicketAndForwardsSignedContextHeaders() {
        CustomerServiceViews.Ticket ticket = ticket("ticket-1");
        when(restTemplate.exchange(
                anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(CustomerServiceViews.Ticket[].class)
        )).thenReturn(ResponseEntity.ok(new CustomerServiceViews.Ticket[]{ticket}));

        ToolViews.CustomerServiceTicketView result = client.list(
                context(), "request-1", "org-1", "student-1", "OPEN", 10
        ).get(0);

        assertEquals("ticket-1", result.getId());
        assertEquals("学生无法预约课程", result.getSubject());

        ArgumentCaptor<HttpEntity> requestCaptor = ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).exchange(
                anyString(), eq(HttpMethod.GET), requestCaptor.capture(), eq(CustomerServiceViews.Ticket[].class)
        );
        HttpHeaders headers = requestCaptor.getValue().getHeaders();
        assertEquals("customer-service-internal-token", headers.getFirst("X-Internal-Service-Token"));
        assertEquals("student-1", headers.getFirst("X-Actor-User-Id"));
        assertEquals("STUDENT", headers.getFirst("X-Actor-Roles"));
        assertEquals("org-1", headers.getFirst("X-Actor-Organization-Ids"));
        assertEquals("request-1", headers.getFirst("X-Request-ID"));
        verifyNoMoreInteractions(restTemplate);
    }

    @Test
    public void forbiddenFromCustomerServiceBecomesGatewayForbidden() {
        when(restTemplate.exchange(
                anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(CustomerServiceViews.Ticket[].class)
        )).thenThrow(clientError(HttpStatus.FORBIDDEN));

        assertFailure(GatewayForbiddenException.class, "授权范围内", () -> client.list(
                context(), "request-403", "org-1", null, null, 20
        ));
    }

    @Test
    public void missingTicketBecomesGatewayNotFound() {
        when(restTemplate.exchange(
                anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(CustomerServiceViews.Ticket.class)
        )).thenThrow(clientError(HttpStatus.NOT_FOUND));

        assertFailure(GatewayResourceNotFoundException.class, "客服工单不存在", () -> client.get(
                context(), "request-404", "org-1", "ticket-missing"
        ));
    }

    @Test
    public void serverErrorAndNetworkFailureBecomeTemporaryUnavailable() {
        when(restTemplate.exchange(
                anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(CustomerServiceViews.Ticket[].class)
        )).thenThrow(serverError()).thenThrow(new ResourceAccessException("连接被拒绝"));

        assertFailure(IllegalStateException.class, "暂时不可用", () -> client.list(
                context(), "request-503", "org-1", null, null, 20
        ));
        assertFailure(IllegalStateException.class, "暂时不可用", () -> client.list(
                context(), "request-network", "org-1", null, null, 20
        ));
    }

    @Test
    public void emptyResponseIsRejectedInsteadOfBeingTreatedAsEmptyList() {
        when(restTemplate.exchange(
                anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(CustomerServiceViews.Ticket[].class)
        )).thenReturn(new ResponseEntity<>(null, HttpStatus.OK));

        assertFailure(IllegalStateException.class, "空响应", () -> client.list(
                context(), "request-empty", "org-1", null, null, 20
        ));
    }

    @Test
    public void missingInternalTokenFailsBeforeCallingCustomerService() {
        properties.setInternalServiceToken(" ");

        assertFailure(IllegalStateException.class, "内部 Token 未配置", () -> client.list(
                context(), "request-token", "org-1", null, null, 20
        ));
        verifyNoMoreInteractions(restTemplate);
    }

    private AgentContext context() {
        return new AgentContext(
                "student-1",
                new HashSet<>(Collections.singletonList("org-1")),
                new HashSet<>(Collections.singletonList(AgentContext.ROLE_STUDENT)),
                Instant.parse("2026-08-12T00:00:00Z"),
                Instant.parse("2026-08-12T00:05:00Z"),
                "nonce"
        );
    }

    private CustomerServiceViews.Ticket ticket(String id) {
        CustomerServiceViews.Ticket ticket = new CustomerServiceViews.Ticket();
        ticket.id = id;
        ticket.organizationId = "org-1";
        ticket.subjectUserId = "student-1";
        ticket.createdByUserId = "student-1";
        ticket.category = "BOOKING_SUPPORT";
        ticket.source = "AGENT";
        ticket.subject = "学生无法预约课程";
        ticket.description = "测试工单";
        ticket.status = "OPEN";
        ticket.createdAt = Instant.parse("2026-08-12T00:00:00Z");
        ticket.updatedAt = ticket.createdAt;
        return ticket;
    }

    private HttpClientErrorException clientError(HttpStatus status) {
        return HttpClientErrorException.create(
                status, status.getReasonPhrase(), HttpHeaders.EMPTY, new byte[0], StandardCharsets.UTF_8
        );
    }

    private HttpServerErrorException serverError() {
        return HttpServerErrorException.create(
                HttpStatus.SERVICE_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE.getReasonPhrase(),
                HttpHeaders.EMPTY, new byte[0], StandardCharsets.UTF_8
        );
    }

    private void assertFailure(Class<? extends Throwable> type, String messagePart, Runnable action) {
        try {
            action.run();
            fail("expected " + type.getSimpleName());
        } catch (Throwable exception) {
            assertEquals(type, exception.getClass());
            assertNotNull(exception.getMessage());
            if (!exception.getMessage().contains(messagePart)) {
                fail("expected message containing '" + messagePart + "' but was: " + exception.getMessage());
            }
        }
    }
}

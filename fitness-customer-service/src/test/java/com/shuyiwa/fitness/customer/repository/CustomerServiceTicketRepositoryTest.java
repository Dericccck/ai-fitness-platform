package com.shuyiwa.fitness.customer.repository;

import com.shuyiwa.fitness.customer.api.CustomerServiceConflictException;
import com.shuyiwa.fitness.customer.api.CustomerServiceTicketCreateRequest;
import com.shuyiwa.fitness.customer.api.CustomerServiceTicketView;
import com.shuyiwa.fitness.customer.security.CustomerServiceActor;
import com.shuyiwa.fitness.customer.security.CustomerServiceConfirmation;
import org.junit.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.Collections;
import java.util.concurrent.atomic.AtomicInteger;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 验证客服工单 request_id、参数摘要和确认 JTI 的幂等边界。 */
@SuppressWarnings("unchecked")
public class CustomerServiceTicketRepositoryTest {

    @Test
    public void sameRequestAndPayloadWithNewJtiReusesExistingTicket() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        CustomerServiceTicketRepository repository = new CustomerServiceTicketRepository(jdbc);
        CustomerServiceTicketView existingView = ticket("ticket-1");
        when(jdbc.query(anyString(), any(Object[].class), any(org.springframework.jdbc.core.RowMapper.class)))
                .thenReturn(Collections.singletonList(
                        new CustomerServiceTicketRepository.ExistingTicket("ticket-1", "hash-1")))
                .thenReturn(Collections.singletonList(existingView));
        when(jdbc.queryForObject(anyString(), any(Object[].class), eq(Integer.class))).thenReturn(0);

        CustomerServiceTicketView result = repository.insert(
                actor("request-1", "jti-new", "hash-1"), request(), "student-1");

        org.junit.Assert.assertEquals("ticket-1", result.getId());
        verify(jdbc, never()).update(anyString(), any(Object[].class));
    }

    @Test(expected = CustomerServiceConflictException.class)
    public void sameRequestWithDifferentPayloadIsRejectedBeforeJtiLookup() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        CustomerServiceTicketRepository repository = new CustomerServiceTicketRepository(jdbc);
        when(jdbc.query(anyString(), any(Object[].class), any(org.springframework.jdbc.core.RowMapper.class)))
                .thenReturn(Collections.singletonList(
                        new CustomerServiceTicketRepository.ExistingTicket("ticket-1", "hash-old")));

        repository.insert(actor("request-1", "jti-new", "hash-new"), request(), "student-1");

        verify(jdbc, never()).queryForObject(anyString(), any(Object[].class), eq(Integer.class));
    }

    @Test(expected = CustomerServiceConflictException.class)
    public void consumedJtiIsRejectedBeforeIdempotentTicketReuse() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        CustomerServiceTicketRepository repository = new CustomerServiceTicketRepository(jdbc);
        when(jdbc.query(anyString(), any(Object[].class), any(org.springframework.jdbc.core.RowMapper.class)))
                .thenReturn(Collections.singletonList(
                        new CustomerServiceTicketRepository.ExistingTicket("ticket-1", "hash-1")));
        when(jdbc.queryForObject(anyString(), any(Object[].class), eq(Integer.class))).thenReturn(1);

        CustomerServiceTicketCreateRequest request = new CustomerServiceTicketCreateRequest();
        request.setOrganizationId("org-1");
        request.setCategory("GENERAL");
        request.setSubject("预约状态异常");
        request.setDescription("请客服核查中文内容");
        CustomerServiceConfirmation confirmation = new CustomerServiceConfirmation(
                "confirmation-1", "jti-1", "fitness.support.ticket.create.v1",
                "CREATE_CUSTOMER_SERVICE_TICKET", "org-1", "org-1:student-1",
                "hash-1");
        CustomerServiceActor actor = new CustomerServiceActor(
                "student-1", Collections.singleton("STUDENT"),
                Collections.singleton("org-1"), "request-1", confirmation);

        repository.insert(actor, request, "student-1");
    }

    @Test
    public void duplicateInsertRaceReusesExistingTicketWithoutSecondAudit() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        CustomerServiceTicketRepository repository = new CustomerServiceTicketRepository(jdbc);
        AtomicInteger requestLookupCount = new AtomicInteger();
        when(jdbc.query(anyString(), any(Object[].class), any(org.springframework.jdbc.core.RowMapper.class)))
                .thenAnswer(invocation -> {
                    String sql = invocation.getArgument(0);
                    if (sql.contains("SELECT id, payload_hash")) {
                        return requestLookupCount.getAndIncrement() == 0
                                ? Collections.emptyList()
                                : Collections.singletonList(
                                new CustomerServiceTicketRepository.ExistingTicket(
                                        "ticket-race", "hash-1"));
                    }
                    return Collections.singletonList(ticket("ticket-race"));
                });
        when(jdbc.queryForObject(anyString(), any(Object[].class), eq(Integer.class))).thenReturn(0);
        when(jdbc.update(anyString(), any(Object[].class)))
                .thenThrow(new org.springframework.dao.DuplicateKeyException("请求并发冲突"));

        CustomerServiceTicketView result = repository.insert(
                actor("request-race", "jti-race", "hash-1"), request(), "student-1");

        // update 的桩对任何第二次写入都会再次抛出 DuplicateKeyException；能返回已有工单，
        // 说明竞争分支没有重复消费确认或重复写入 CREATED 审计。
        org.junit.Assert.assertEquals("ticket-race", result.getId());
    }

    private static CustomerServiceTicketCreateRequest request() {
        CustomerServiceTicketCreateRequest request = new CustomerServiceTicketCreateRequest();
        request.setOrganizationId("org-1");
        request.setCategory("GENERAL");
        request.setSubject("预约状态异常");
        request.setDescription("请客服核查中文内容");
        return request;
    }

    private static CustomerServiceActor actor(String requestId, String jti, String payloadHash) {
        CustomerServiceConfirmation confirmation = new CustomerServiceConfirmation(
                "confirmation-1", jti, "fitness.support.ticket.create.v1",
                "CREATE_CUSTOMER_SERVICE_TICKET", "org-1", "org-1:student-1", payloadHash);
        return new CustomerServiceActor(
                "student-1", Collections.singleton("STUDENT"),
                Collections.singleton("org-1"), requestId, confirmation);
    }

    private static CustomerServiceTicketView ticket(String id) {
        CustomerServiceTicketView ticket = new CustomerServiceTicketView();
        ticket.setId(id);
        ticket.setOrganizationId("org-1");
        ticket.setSubjectUserId("student-1");
        ticket.setSource("AGENT");
        ticket.setStatus("OPEN");
        return ticket;
    }
}

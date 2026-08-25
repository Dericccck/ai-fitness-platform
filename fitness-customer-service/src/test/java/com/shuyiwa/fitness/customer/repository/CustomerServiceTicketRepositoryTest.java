package com.shuyiwa.fitness.customer.repository;

import com.shuyiwa.fitness.customer.api.CustomerServiceConflictException;
import com.shuyiwa.fitness.customer.api.CustomerServiceTicketCreateRequest;
import com.shuyiwa.fitness.customer.security.CustomerServiceActor;
import com.shuyiwa.fitness.customer.security.CustomerServiceConfirmation;
import org.junit.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.Collections;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** 验证同一已消费 JTI 不能被幂等查询路径当作成功重放。 */
@SuppressWarnings("unchecked")
public class CustomerServiceTicketRepositoryTest {

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
}

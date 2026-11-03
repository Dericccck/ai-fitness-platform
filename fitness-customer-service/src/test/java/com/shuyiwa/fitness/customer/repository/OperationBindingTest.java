package com.shuyiwa.fitness.customer.repository;

import com.shuyiwa.fitness.customer.security.CustomerServiceConfirmation;
import com.shuyiwa.fitness.customer.api.CustomerServiceConflictException;
import org.junit.Before;
import org.junit.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import com.shuyiwa.fitness.customer.testsupport.IsolatedJdbcTest;
import static org.junit.Assert.*;

/** Uses the real JDBC binding check, including joins to the original immutable actor. */
public class OperationBindingTest extends IsolatedJdbcTest {
    private static final String HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private JdbcTemplate jdbc;
    private CustomerServiceTicketRepository repository;
    @Before public void setUp() {
        jdbc = new JdbcTemplate(newDataSource());
        repository = new CustomerServiceTicketRepository(jdbc);
        jdbc.execute("CREATE TABLE agent_customer_service_confirmation_consumption (request_id VARCHAR(128),confirmation_id VARCHAR(128),tool_id VARCHAR(128),action VARCHAR(128),organization_id VARCHAR(128),resource VARCHAR(128),payload_hash VARCHAR(64))");
        jdbc.execute("CREATE TABLE agent_customer_service_ticket (create_request_id VARCHAR(128),created_by_user_id VARCHAR(128))");
        jdbc.update("INSERT INTO agent_customer_service_ticket VALUES (?,?)", "op-1", "actor-1");
        jdbc.update("INSERT INTO agent_customer_service_confirmation_consumption VALUES (?,?,?,?,?,?,?)", "op-1", "confirmation-1", "tool-1", "action-1", "org-1", "resource-1", HASH);
    }
    @Test public void identicalOperationBindingCanBeCheckedWithoutWritingAgain() {
        repository.assertRequestBinding("op-1", "actor-1", confirmation("confirmation-1", "tool-1", "action-1", "org-1", "resource-1", HASH));
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM agent_customer_service_confirmation_consumption", Integer.class).intValue());
    }
    @Test public void operationIdCannotBeReboundToDifferentPayloadToolActionOrActor() {
        String[][] variants = {
            {"confirmation-1","tool-1","action-1","org-1","resource-1",HASH.replace('a','b')},
            {"confirmation-1","tool-other","action-1","org-1","resource-1",HASH},
            {"confirmation-1","tool-1","action-other","org-1","resource-1",HASH},
            {"confirmation-other","tool-1","action-1","org-1","resource-1",HASH},
            {"confirmation-1","tool-1","action-1","org-other","resource-1",HASH},
            {"confirmation-1","tool-1","action-1","org-1","resource-other",HASH}
        };
        for (String[] v : variants) {
            try { repository.assertRequestBinding("op-1", "actor-1", confirmation(v[0],v[1],v[2],v[3],v[4],v[5])); fail("mismatched binding accepted"); }
            catch (CustomerServiceConflictException expected) { }
        }
        try { repository.assertRequestBinding("op-1", "actor-other", confirmation("confirmation-1","tool-1","action-1","org-1","resource-1",HASH)); fail("actor mismatch accepted"); }
        catch (CustomerServiceConflictException expected) { }
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM agent_customer_service_confirmation_consumption", Integer.class).intValue());
    }
    private CustomerServiceConfirmation confirmation(String id,String tool,String action,String org,String resource,String hash) {
        return new CustomerServiceConfirmation(id,"retry-jti",tool,action,org,resource,hash);
    }
}

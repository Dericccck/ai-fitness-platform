package com.shuyiwa.fitness.customer.service;

import com.shuyiwa.fitness.customer.api.ToolOperationView;
import org.junit.Before;
import org.junit.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;
import com.shuyiwa.fitness.customer.testsupport.IsolatedJdbcTest;
import static org.junit.Assert.*;

/** Executes the actual query SQL and rollback semantics against an isolated transactional database. */
public class ToolOperationQueryServiceTest extends IsolatedJdbcTest {
    private static final String HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private JdbcTemplate jdbc;
    private ToolOperationQueryService service;
    private TransactionTemplate tx;
    @Before public void setUp() {
        DriverManagerDataSource ds = newDataSource();
        jdbc = new JdbcTemplate(ds); tx = new TransactionTemplate(new DataSourceTransactionManager(ds));
        service = new ToolOperationQueryService(jdbc);
        jdbc.execute("CREATE TABLE agent_customer_service_confirmation_consumption (request_id VARCHAR(128),tool_id VARCHAR(128),confirmation_id VARCHAR(128),organization_id VARCHAR(128),payload_hash VARCHAR(64),action VARCHAR(128))");
        jdbc.execute("CREATE TABLE agent_customer_service_ticket (id VARCHAR(128),create_request_id VARCHAR(128),organization_id VARCHAR(128),payload_hash VARCHAR(64),created_by_user_id VARCHAR(128))");
        jdbc.execute("CREATE TABLE agent_customer_service_ticket_audit (request_id VARCHAR(128),ticket_id VARCHAR(128),actor_id VARCHAR(128),action VARCHAR(128))");
    }
    @Test public void everySupportedWriteRequiresBothCommittedFactsAndMatchingConfirmation() {
        String[][] cases = {{"fitness.support.ticket.create.v1", "CREATE_CUSTOMER_SERVICE_TICKET"}};
        for (String[] item : cases) {
            setUp(); String tool = item[0], action = item[1];
            assertEquals("UNKNOWN", query("operation-1", tool).getStatus());
            consume("operation-1", tool, action);
            assertEquals("consumption alone is insufficient", "UNKNOWN", query("operation-1", tool).getStatus());
            fact("operation-1", tool, action);
            ToolOperationView result = query("operation-1", tool);
            assertEquals("SUCCEEDED", result.getStatus()); assertEquals("result-1", result.getResultReference());
            rejectScope(tool, "confirmation-other", "org-1", "actor-1", HASH);
            rejectScope(tool, "confirmation-1", "org-other", "actor-1", HASH);
            rejectScope(tool, "confirmation-1", "org-1", "actor-other", HASH);
            rejectScope(tool, "confirmation-1", "org-1", "actor-1", HASH.replace('a','b'));
        }
    }
    @Test public void rolledBackBusinessAndConfirmationNeverLookSuccessful() {
        String tool = "fitness.support.ticket.create.v1", action = "CREATE_CUSTOMER_SERVICE_TICKET";
        tx.execute(status -> { consume("operation-1", tool, action); fact("operation-1", tool, action); status.setRollbackOnly(); return null; });
        assertEquals("UNKNOWN", query("operation-1", tool).getStatus());
    }
    @Test public void businessFactWithoutConfirmationIsUnknown() {
        String tool = "fitness.support.ticket.create.v1", action = "CREATE_CUSTOMER_SERVICE_TICKET";
        fact("operation-1", tool, action);
        assertEquals("UNKNOWN", query("operation-1", tool).getStatus());
    }
    private void rejectScope(String tool, String confirmation, String org, String actor, String hash) {
        try { service.query("operation-1", tool, confirmation, org, actor, hash); fail("scope mismatch accepted"); }
        catch (ResponseStatusException expected) { assertEquals(403, expected.getStatus().value()); }
    }
    private ToolOperationView query(String op, String tool) { return service.query(op, tool, "confirmation-1", "org-1", "actor-1", HASH); }
    @Test public void leadingPunctuationInStableIdsPreservesCommittedResultBinding() {
        String tool = "fitness.support.ticket.create.v1", action = "CREATE_CUSTOMER_SERVICE_TICKET";
        for (String prefix : new String[]{"_-", ":", ".", "-"}) {
            setUp();
            String operation = prefix + "operation", confirmation = prefix + "confirmation";
            String org = prefix + "org", actor = prefix + "actor";
            consume(operation, tool, action, confirmation, org);
            fact(operation, tool, action, org, actor);
            ToolOperationView result = service.query(operation, tool, confirmation, org, actor, HASH);
            assertEquals("SUCCEEDED", result.getStatus());
            assertEquals(operation, result.getOperationId());
            assertEquals(confirmation, result.getConfirmationId());
            assertEquals(org, result.getOrganizationId());
            assertEquals(actor, result.getActorId());
        }
    }
    @Test public void unsafeOrNormalizedPathIdentitiesAreRejectedInEveryScopeField() {
        String tool = "fitness.support.ticket.create.v1";
        for (String invalid : new String[]{null, "", ".", "..", "../admin", "op/admin", "%2e%2e", "op%2fadmin",
                "op\\admin", "with space", new String(new char[129]).replace('\0', 'a')}) {
            for (int field = 0; field < 4; field++) {
                String[] scope = {"operation-1", "confirmation-1", "org-1", "actor-1"};
                scope[field] = invalid;
                try { service.query(scope[0], tool, scope[1], scope[2], scope[3], HASH); fail("unsafe identity accepted"); }
                catch (ResponseStatusException expected) { assertEquals(400, expected.getStatus().value()); }
            }
        }
    }
    private void consume(String op, String tool, String action) {
        consume(op, tool, action, "confirmation-1", "org-1");
    }
    private void consume(String op, String tool, String action, String confirmation, String org) {
        jdbc.update("INSERT INTO agent_customer_service_confirmation_consumption VALUES (?,?,?,?,?,?)", op, tool, confirmation, org, HASH, action);
    }
    private void fact(String op, String tool, String action) {
        fact(op, tool, action, "org-1", "actor-1");
    }
    private void fact(String op, String tool, String action, String org, String actor) {
        jdbc.update("INSERT INTO agent_customer_service_ticket VALUES (?,?,?,?,?)", "result-1", op, org, HASH, actor);
        jdbc.update("INSERT INTO agent_customer_service_ticket_audit VALUES (?,?,?,?)", op, "result-1", actor, "CREATED");
    }
}

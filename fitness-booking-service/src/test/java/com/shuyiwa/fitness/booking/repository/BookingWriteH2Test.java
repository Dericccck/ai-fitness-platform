package com.shuyiwa.fitness.booking.repository;

import com.shuyiwa.fitness.booking.api.BookingApiException;
import com.shuyiwa.fitness.booking.api.BookingAppointmentView;
import com.shuyiwa.fitness.booking.api.BookingCreateRequest;
import com.shuyiwa.fitness.booking.security.BookingConfirmation;
import org.junit.Before;
import org.junit.Test;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionTemplate;

import java.sql.Date;
import java.time.Instant;
import java.util.UUID;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.fail;

/**
 * Transaction component tests against a private H2 in-memory schema.
 * This fixture deliberately does not emulate MySQL locks or production schema migrations.
 */
public class BookingWriteH2Test {
    private JdbcTemplate jdbc;
    private TransactionTemplate transaction;
    private BookingRepository repository;

    @Before
    public void setup() {
        DriverManagerDataSource source = new DriverManagerDataSource(
                "jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1", "sa", "");
        jdbc = new JdbcTemplate(source);
        transaction = new TransactionTemplate(new DataSourceTransactionManager(source));
        repository = new BookingRepository(jdbc);
        jdbc.execute("CREATE TABLE contract (id VARCHAR(128) PRIMARY KEY, remaining_class_hours INT, version BIGINT)");
        jdbc.execute("CREATE TABLE appointment (id VARCHAR(128) PRIMARY KEY, status INT, course_name VARCHAR(128), course_id VARCHAR(128), course_start_date DATE, course_start_time TIMESTAMP, course_end_time TIMESTAMP, create_login_user_id VARCHAR(128), last_update_login_user_id VARCHAR(128), head_coach_ids VARCHAR(128), user_id VARCHAR(128), coach_id VARCHAR(128), organization_id VARCHAR(128), contract_id VARCHAR(128), mark INT, amount VARCHAR(32), deleted INT)");
        jdbc.execute("CREATE TABLE agent_booking_confirmation_consumption (jti VARCHAR(128) PRIMARY KEY, confirmation_id VARCHAR(128), tool_id VARCHAR(128), action VARCHAR(128), organization_id VARCHAR(128), resource VARCHAR(128), request_id VARCHAR(128), payload_hash VARCHAR(128))");
        jdbc.execute("CREATE TABLE agent_booking_operation (request_id VARCHAR(128) PRIMARY KEY, appointment_id VARCHAR(128), organization_id VARCHAR(128), actor_id VARCHAR(128))");
        jdbc.execute("CREATE TABLE agent_booking_audit (appointment_id VARCHAR(128), organization_id VARCHAR(128), action VARCHAR(128), actor_id VARCHAR(128), request_id VARCHAR(128))");
        jdbc.execute("CREATE TABLE agent_booking_outbox (event_key VARCHAR(128) PRIMARY KEY, event_type VARCHAR(128), aggregate_id VARCHAR(128), organization_id VARCHAR(128), aggregate_version BIGINT, payload VARCHAR(2000))");
        jdbc.update("INSERT INTO contract VALUES ('contract-1', 2, 0)");
    }

    @Test
    public void createsBookingAndAtomicallyRecordsConsumptionAndAudit() {
        BookingAppointmentView result = transaction.execute(status -> write("request-1", "jti-1", 0, 1));
        assertNotNull(result);
        assertEquals(1, result.getRemainingClassHours().intValue());
        assertState(1, 1, 1, 1, 1, 1);
    }

    @Test
    public void reusedJtiRollsBackBookingAndDeduction() {
        transaction.execute(status -> write("request-1", "jti-1", 0, 1));
        try {
            transaction.execute(status -> write("request-2", "jti-1", 1, 0));
            fail("reused JTI must be rejected");
        } catch (BookingApiException expected) {
            assertEquals(409, expected.getStatus().value());
        }
        assertState(1, 1, 1, 1, 1, 1);
    }

    @Test
    public void staleContractVersionRejectsWithoutPartialWrites() {
        transaction.execute(status -> write("request-1", "jti-1", 0, 1));
        try {
            transaction.execute(status -> write("request-2", "jti-2", 0, 0));
            fail("stale contract version must be rejected");
        } catch (BookingApiException expected) {
            assertEquals(409, expected.getStatus().value());
        }
        assertState(1, 1, 1, 1, 1, 1);
    }

    @Test
    public void auditFailureRollsBackAllEarlierWrites() {
        jdbc.execute("ALTER TABLE agent_booking_audit ADD CONSTRAINT reject_audit CHECK (action <> 'CREATE_APPOINTMENT')");
        try {
            transaction.execute(status -> write("request-1", "jti-1", 0, 1));
            fail("injected audit failure must abort the transaction");
        } catch (DataIntegrityViolationException expected) {
            // The injected database constraint rejects the audit after earlier writes.
        }
        assertState(2, 0, 0, 0, 0, 0);
    }

    private BookingAppointmentView write(String requestId, String jti, long version, int remaining) {
        BookingCreateRequest request = new BookingCreateRequest();
        request.setOrganizationId("org-1");
        request.setStudentId("student-1");
        request.setContractId("contract-1");
        request.setCoachId("coach-1");
        request.setCourseId("course-1");
        request.setStartTime(Instant.parse("2030-01-01T02:00:00Z"));
        request.setEndTime(Instant.parse("2030-01-01T03:00:00Z"));
        BookingRepository.ContractRecord contract = new BookingRepository.ContractRecord(
                "contract-1", "org-1", "student-1", "course-1", Date.valueOf("2029-01-01"),
                Date.valueOf("2031-01-01"), remaining + 1, 1, version);
        BookingConfirmation confirmation = new BookingConfirmation("confirmation-" + requestId, jti,
                "fitness.booking.create.v1", "CREATE_APPOINTMENT", "org-1", "contract-1",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        return repository.insertBooking(request, new BookingRepository.BookingActorData("student-1", requestId),
                contract, new BookingRepository.CourseRecord("course-1", "力量训练", 1), "coach-1",
                confirmation, remaining);
    }

    private void assertState(int remaining, int bookings, int consumptions, int operations, int audit, int outbox) {
        assertEquals(remaining, jdbc.queryForObject("SELECT remaining_class_hours FROM contract WHERE id='contract-1'", Integer.class).intValue());
        assertEquals(bookings, count("appointment"));
        assertEquals(consumptions, count("agent_booking_confirmation_consumption"));
        assertEquals(operations, count("agent_booking_operation"));
        assertEquals(audit, count("agent_booking_audit"));
        assertEquals(outbox, count("agent_booking_outbox"));
    }

    private int count(String table) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class).intValue();
    }
}

package com.shuyiwa.fitness.booking.repository;

import com.shuyiwa.fitness.booking.api.BookingApiException;
import com.shuyiwa.fitness.booking.api.BookingAppointmentView;
import com.shuyiwa.fitness.booking.api.BookingCancelRequest;
import com.shuyiwa.fitness.booking.api.BookingCancelledView;
import com.shuyiwa.fitness.booking.api.BookingCreateRequest;
import com.shuyiwa.fitness.booking.api.BookingRescheduleRequest;
import com.shuyiwa.fitness.booking.config.BookingDatabaseSchema;
import com.shuyiwa.fitness.booking.security.BookingActor;
import com.shuyiwa.fitness.booking.security.BookingConfirmation;
import com.shuyiwa.fitness.booking.service.BookingService;
import org.junit.Assume;
import org.junit.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionTemplate;

import javax.sql.DataSource;
import java.sql.Date;
import java.time.Instant;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;

/**
 * 真实 MySQL 预约写链路集成测试。
 *
 * <p>该测试默认跳过，只有显式设置 BOOKING_IT_ENABLED=true 并提供独立测试库连接时才执行。
 * 测试会使用随机业务 ID 写入现有健身表，结束后删除自身数据，不会依赖 mock，也不会默认连接
 * 开发库。它重点验证单元测试无法覆盖的数据库行为：真实旧表字段、事务提交、合同版本扣减、
 * MySQL GET_LOCK、幂等记录、改约、取消、课时恢复和确认 JTI 冲突回滚。</p>
 */
public class BookingRepositoryIntegrationTest {
    private static final String HASH =
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    @Test
    public void createsReschedulesCancelsAndRollsBackWhenConfirmationJtiIsReused() {
        Assume.assumeTrue("true".equalsIgnoreCase(System.getenv("BOOKING_IT_ENABLED")));

        Fixture fixture = new Fixture();
        JdbcTemplate jdbc = new JdbcTemplate(fixture.dataSource);
        BookingService service = new BookingService(new BookingRepository(jdbc));
        TransactionTemplate transaction = new TransactionTemplate(
                new DataSourceTransactionManager(fixture.dataSource));
        try {
            fixture.createSchema(jdbc);
            fixture.insertBusinessFacts(jdbc);

            BookingAppointmentView first = transaction.execute(status ->
                    service.create(fixture.actor("request-1"), fixture.request(
                            Instant.parse("2026-09-01T02:00:00Z"),
                            Instant.parse("2026-09-01T03:00:00Z"))));
            assertNotNull(first);
            assertEquals(1, first.getStatus().intValue());
            assertEquals(1, first.getRemainingClassHours().intValue());
            assertEquals(1, remainingHours(jdbc, fixture.contractId));
            assertEquals(1, countAppointments(jdbc, fixture.contractId, fixture.studentId));

            // 相同 request_id 重试只返回第一次结果，不会再次扣减课时或创建预约。
            BookingAppointmentView retry = transaction.execute(status ->
                    service.create(fixture.actor("request-1"), fixture.request(
                            Instant.parse("2026-09-01T02:00:00Z"),
                            Instant.parse("2026-09-01T03:00:00Z"))));
            assertEquals(first.getId(), retry.getId());
            assertEquals(1, remainingHours(jdbc, fixture.contractId));
            assertEquals(1, countAppointments(jdbc, fixture.contractId, fixture.studentId));

            // 复用已经消费的 JTI，但更换 request_id 和时间段，必须在事务内失败并回滚预约及课时扣减。
            try {
                transaction.execute(status -> service.create(
                        fixture.actor("request-2"), fixture.request(
                                Instant.parse("2026-09-01T04:00:00Z"),
                                Instant.parse("2026-09-01T05:00:00Z"))));
                throw new AssertionError("reused confirmation JTI must be rejected");
            } catch (BookingApiException expected) {
                assertEquals(409, expected.getStatus().value());
            }
            assertEquals("JTI conflict must roll back contract deduction", 1,
                    remainingHours(jdbc, fixture.contractId));
            assertEquals("JTI conflict must roll back appointment insert", 1,
                    countAppointments(jdbc, fixture.contractId, fixture.studentId));

            BookingAppointmentView rescheduled = transaction.execute(status ->
                    service.reschedule(fixture.rescheduleActor("request-3", first.getId()),
                            fixture.rescheduleRequest(first.getId(),
                                    Instant.parse("2026-09-01T04:00:00Z"),
                                    Instant.parse("2026-09-01T05:00:00Z"))));
            assertEquals(first.getId(), rescheduled.getId());
            assertEquals(Instant.parse("2026-09-01T04:00:00Z"), rescheduled.getStartTime());
            assertEquals(2L, maxAggregateVersion(jdbc, first.getId()));
            assertEquals(1, remainingHours(jdbc, fixture.contractId));

            // 第二个预约用于验证取消第一个预约时，旧系统后续预约的 amount 快照同步加回 1。
            BookingAppointmentView second = transaction.execute(status ->
                    service.create(fixture.createActor("request-5", "it-create-jti-2-" + fixture.suffix),
                            fixture.request(Instant.parse("2026-09-01T06:00:00Z"),
                                    Instant.parse("2026-09-01T07:00:00Z"))));
            assertEquals(0, remainingHours(jdbc, fixture.contractId));
            assertEquals("0", appointmentAmount(jdbc, second.getId()));

            BookingCancelledView cancelled = transaction.execute(status ->
                    service.cancel(fixture.cancelActor("request-4", rescheduled.getId()),
                            fixture.cancelRequest(rescheduled.getId(), rescheduled.getStartTime())));
            assertEquals(rescheduled.getId(), cancelled.getId());
            assertEquals(1, cancelled.getRemainingClassHours().intValue());
            assertEquals(1, remainingHours(jdbc, fixture.contractId));
            assertEquals(1, countActiveAppointments(jdbc, fixture.contractId, fixture.studentId));
            assertEquals("1", appointmentAmount(jdbc, second.getId()));
            assertEquals(1, countOutboxEvents(jdbc, "APPOINTMENT_CANCELLED", rescheduled.getId()));
            assertEquals(3L, maxAggregateVersion(jdbc, rescheduled.getId()));

            // 相同 request_id 重试取消只复用第一次结果，不会再次退回课时。
            BookingCancelledView cancelRetry = transaction.execute(status ->
                    service.cancel(fixture.cancelActor("request-4", rescheduled.getId()),
                            fixture.cancelRequest(rescheduled.getId(), rescheduled.getStartTime())));
            assertEquals(cancelled.getId(), cancelRetry.getId());
            assertEquals(1, remainingHours(jdbc, fixture.contractId));
            assertEquals(1, countActiveAppointments(jdbc, fixture.contractId, fixture.studentId));
        } finally {
            fixture.cleanup(jdbc);
        }
    }

    private static int remainingHours(JdbcTemplate jdbc, String contractId) {
        Integer value = jdbc.queryForObject(
                "SELECT remaining_class_hours FROM contract WHERE id = ?", Integer.class,
                contractId);
        return value == null ? -1 : value;
    }

    private static int countAppointments(JdbcTemplate jdbc, String contractId, String studentId) {
        Integer value = jdbc.queryForObject(
                "SELECT COUNT(1) FROM appointment WHERE contract_id = ? AND user_id = ?",
                Integer.class, contractId, studentId);
        return value == null ? 0 : value;
    }

    private static int countActiveAppointments(JdbcTemplate jdbc, String contractId, String studentId) {
        Integer value = jdbc.queryForObject(
                "SELECT COUNT(1) FROM appointment WHERE contract_id = ? AND user_id = ? AND deleted = 0",
                Integer.class, contractId, studentId);
        return value == null ? 0 : value;
    }

    private static String appointmentAmount(JdbcTemplate jdbc, String appointmentId) {
        return jdbc.queryForObject("SELECT amount FROM appointment WHERE id = ?", String.class, appointmentId);
    }

    private static int countOutboxEvents(JdbcTemplate jdbc, String eventType, String aggregateId) {
        Integer value = jdbc.queryForObject(
                "SELECT COUNT(1) FROM agent_booking_outbox WHERE event_type = ? AND aggregate_id = ?",
                Integer.class, eventType, aggregateId);
        return value == null ? 0 : value;
    }

    private static long maxAggregateVersion(JdbcTemplate jdbc, String aggregateId) {
        Long value = jdbc.queryForObject(
                "SELECT MAX(aggregate_version) FROM agent_booking_outbox WHERE aggregate_id = ?",
                Long.class, aggregateId);
        return value == null ? 0L : value;
    }

    private static Set<String> set(String value) {
        return new HashSet<>(Collections.singleton(value));
    }

    private static final class Fixture {
        private final String suffix = UUID.randomUUID().toString().replace("-", "").substring(0, 8);
        private final String organizationId = "it-org-" + suffix;
        private final String studentId = "it-student-" + suffix;
        private final String coachId = "it-coach-" + suffix;
        private final String studentPhone = "138" + suffix;
        private final String coachPhone = "139" + suffix;
        private final String courseId = "it-course-" + suffix;
        private final String contractId = "it-contract-" + suffix;
        private final String relationId = "it-relation-" + suffix;
        private final String authorityId = "it-authority-" + suffix;
        private final DataSource dataSource = new DriverManagerDataSource(
                required("BOOKING_IT_DB_URL"), required("BOOKING_IT_DB_USERNAME"),
                required("BOOKING_IT_DB_PASSWORD"));

        private void createSchema(JdbcTemplate jdbc) {
            BookingDatabaseSchema.initialize(dataSource, jdbc);
        }

        private void insertBusinessFacts(JdbcTemplate jdbc) {
            // 旧业务表存在真实外键，测试夹具必须按“用户 -> 组织 -> 课程/关系 -> 合同”的顺序造数。
            // 手机号使用随机后缀，避免和本地已有账号的唯一索引冲突；连接 URL 已显式指定 UTF-8，
            // 这里的中文课程名会作为一次真实的中文写入链路参与验证。
            jdbc.update("INSERT INTO login_user (id, name, phone, daily_votes, enabled, version) "
                            + "VALUES (?, ?, ?, 0, 1, 0)",
                    studentId, "集成测试学员-" + suffix, studentPhone);
            jdbc.update("INSERT INTO login_user (id, name, phone, daily_votes, enabled, version) "
                            + "VALUES (?, ?, ?, 0, 1, 0)",
                    coachId, "集成测试教练-" + suffix, coachPhone);
            jdbc.update("INSERT INTO organization (id, name, priority, version) VALUES (?, ?, 0, 0)",
                    organizationId, "集成测试门店-" + suffix);
            jdbc.update("INSERT INTO course (id, name, status, organization_id, create_time) "
                            + "VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)",
                    courseId, "集成测试力量训练", organizationId);
            jdbc.update("INSERT INTO login_user_authority "
                            + "(id, login_user_id, authority, entity_id, create_time) "
                            + "VALUES (?, ?, 'COACH', ?, CURRENT_TIMESTAMP)",
                    authorityId, coachId, organizationId);
            jdbc.update("INSERT INTO user_and_coach "
                            + "(id, user_id, coach_id, organization_id, status, deleted, head_coach_ids) "
                            + "VALUES (?, ?, ?, ?, 1, 0, ?)",
                    relationId, studentId, coachId, organizationId, coachId);
            jdbc.update("INSERT INTO contract "
                            + "(id, organization_id, user_id, course_id, contract_create_time, "
                            + "contract_end_time, remaining_class_hours, status, version, deleted) "
                            + "VALUES (?, ?, ?, ?, ?, ?, 2, 1, 0, 0)",
                    contractId, organizationId, studentId, courseId,
                    Date.valueOf("2026-01-01"), Date.valueOf("2026-12-31"));
        }

        private BookingCreateRequest request(Instant start, Instant end) {
            BookingCreateRequest request = new BookingCreateRequest();
            request.setOrganizationId(organizationId);
            request.setStudentId(studentId);
            request.setContractId(contractId);
            request.setCoachId(coachId);
            request.setCourseId(courseId);
            request.setStartTime(start);
            request.setEndTime(end);
            return request;
        }

        private BookingActor actor(String requestId) {
            return createActor(requestId, "it-create-jti-" + suffix);
        }

        private BookingActor createActor(String requestId, String jti) {
            return new BookingActor(studentId, set(BookingActor.STUDENT), set(organizationId), requestId,
                    new BookingConfirmation("it-confirmation-" + requestId + "-" + suffix, jti,
                            "fitness.booking.create.v1", "CREATE_APPOINTMENT", organizationId,
                            contractId, HASH));
        }

        private BookingActor rescheduleActor(String requestId, String appointmentId) {
            return new BookingActor(studentId, set(BookingActor.STUDENT), set(organizationId), requestId,
                    new BookingConfirmation("it-reschedule-confirmation-" + suffix,
                            "it-reschedule-jti-" + suffix, "fitness.booking.reschedule.v1",
                            "RESCHEDULE_APPOINTMENT", organizationId, appointmentId, HASH));
        }

        private BookingActor cancelActor(String requestId, String appointmentId) {
            return new BookingActor(studentId, set(BookingActor.STUDENT), set(organizationId), requestId,
                    new BookingConfirmation("it-cancel-confirmation-" + suffix,
                            "it-cancel-jti-" + suffix, "fitness.booking.cancel.v1",
                            "CANCEL_APPOINTMENT", organizationId, appointmentId, HASH));
        }

        private BookingRescheduleRequest rescheduleRequest(String appointmentId, Instant start, Instant end) {
            BookingRescheduleRequest request = new BookingRescheduleRequest();
            request.setOrganizationId(organizationId);
            request.setAppointmentId(appointmentId);
            request.setCoachId(coachId);
            request.setExpectedStartTime(Instant.parse("2026-09-01T02:00:00Z"));
            request.setStartTime(start);
            request.setEndTime(end);
            return request;
        }

        private BookingCancelRequest cancelRequest(String appointmentId, Instant expectedStart) {
            BookingCancelRequest request = new BookingCancelRequest();
            request.setOrganizationId(organizationId);
            request.setAppointmentId(appointmentId);
            request.setExpectedStartTime(expectedStart);
            return request;
        }

        private void cleanup(JdbcTemplate jdbc) {
            jdbc.update("DELETE FROM agent_booking_outbox WHERE aggregate_id IN "
                    + "(SELECT id FROM appointment WHERE contract_id = ? AND user_id = ?)",
                    contractId, studentId);
            jdbc.update("DELETE FROM agent_booking_audit WHERE request_id IN ('request-1', 'request-2')");
            jdbc.update("DELETE FROM agent_booking_audit WHERE request_id IN ('request-3', 'request-4', 'request-5')");
            jdbc.update("DELETE FROM agent_booking_confirmation_consumption WHERE request_id IN "
                    + "('request-1', 'request-2', 'request-3', 'request-4', 'request-5')");
            jdbc.update("DELETE FROM agent_booking_operation WHERE request_id IN ('request-1', 'request-2', 'request-5')");
            jdbc.update("DELETE FROM agent_booking_reschedule_operation WHERE request_id = 'request-3'");
            jdbc.update("DELETE FROM agent_booking_cancel_operation WHERE request_id = 'request-4'");
            jdbc.update("DELETE FROM appointment WHERE contract_id = ? AND user_id = ?",
                    contractId, studentId);
            jdbc.update("DELETE FROM contract WHERE id = ?", contractId);
            jdbc.update("DELETE FROM user_and_coach WHERE id = ?", relationId);
            jdbc.update("DELETE FROM login_user_authority WHERE id = ?", authorityId);
            jdbc.update("DELETE FROM course WHERE id = ?", courseId);
            jdbc.update("DELETE FROM organization WHERE id = ?", organizationId);
            jdbc.update("DELETE FROM login_user WHERE id IN (?, ?)", studentId, coachId);
        }
    }

    private static String required(String name) {
        String value = System.getenv(name);
        if (value == null || value.trim().isEmpty()) {
            throw new IllegalStateException(name + " is required when BOOKING_IT_ENABLED=true");
        }
        return value;
    }
}

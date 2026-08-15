package com.shuyiwa.fitness.booking.repository;

import com.shuyiwa.fitness.booking.api.BookingApiException;
import com.shuyiwa.fitness.booking.api.BookingCreateRequest;
import com.shuyiwa.fitness.booking.api.BookingAppointmentView;
import com.shuyiwa.fitness.booking.domain.AppointmentStatusCodes;
import com.shuyiwa.fitness.booking.security.BookingConfirmation;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Date;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * 预约写服务的显式 SQL 仓储。
 *
 * <p>所有会修改旧业务表的语句都在一个 InnoDB 事务中执行。合同行先锁定，再校验和扣减
 * 课时；教练日期使用 MySQL 命名锁串行化，避免两个不同请求同时通过冲突检查。</p>
 */
@Repository
public class BookingRepository {
    private static final ZoneId BUSINESS_ZONE = ZoneId.of("Asia/Shanghai");
    private final JdbcTemplate jdbc;

    public BookingRepository(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    public Optional<BookingAppointmentView> findByRequestId(String requestId) {
        List<BookingAppointmentView> result = jdbc.query(
                "SELECT a.id, a.organization_id, a.user_id, a.coach_id, a.course_id, a.course_name, "
                        + "a.course_start_time, a.course_end_time, a.status, a.contract_id, "
                        + "c.remaining_class_hours "
                        + "FROM agent_booking_operation o JOIN appointment a ON a.id = o.appointment_id "
                        + "LEFT JOIN contract c ON c.id = a.contract_id WHERE o.request_id = ? LIMIT 1",
                new Object[]{requestId}, (rs, rowNum) -> mapAppointment(rs));
        return result.stream().findFirst();
    }

    public ContractRecord findContractForUpdate(String organizationId, String studentId, String contractId) {
        List<ContractRecord> result = jdbc.query(
                "SELECT id, organization_id, user_id, course_id, contract_create_time, contract_end_time, "
                        + "remaining_class_hours, status, version FROM contract "
                        + "WHERE id = ? AND organization_id = ? AND user_id = ? AND deleted = 0 FOR UPDATE",
                new Object[]{contractId, organizationId, studentId}, (rs, rowNum) -> new ContractRecord(
                        rs.getString("id"), rs.getString("organization_id"), rs.getString("user_id"),
                        rs.getString("course_id"), rs.getDate("contract_create_time"),
                        rs.getDate("contract_end_time"), rs.getInt("remaining_class_hours"),
                        rs.getInt("status"), rs.getLong("version")
                ));
        return result.stream().findFirst().orElseThrow(
                () -> new BookingApiException(HttpStatus.NOT_FOUND, "合同不存在或不属于当前学员"));
    }

    public Optional<CourseRecord> findActiveCourse(String organizationId, String courseId) {
        List<CourseRecord> result = jdbc.query(
                "SELECT id, name, status FROM course WHERE id = ? AND organization_id = ? LIMIT 1",
                new Object[]{courseId, organizationId},
                (rs, rowNum) -> new CourseRecord(rs.getString("id"), rs.getString("name"), rs.getInt("status")));
        return result.stream().findFirst();
    }

    public boolean isOrganizationMember(String organizationId, String studentId) {
        return count("SELECT COUNT(1) FROM user_and_coach WHERE organization_id = ? AND user_id = ? "
                + "AND deleted = 0 AND status IN (0, 1, 4)", organizationId, studentId) > 0;
    }

    public boolean isCoachForStudent(String organizationId, String coachId, String studentId) {
        return count("SELECT COUNT(1) FROM user_and_coach WHERE organization_id = ? AND coach_id = ? "
                + "AND user_id = ? AND deleted = 0 AND status = 1", organizationId, coachId, studentId) > 0;
    }

    public boolean isCoachInOrganization(String organizationId, String coachId) {
        return count("SELECT COUNT(1) FROM login_user_authority WHERE login_user_id = ? "
                + "AND entity_id = ? AND authority IN ('COACH', 'ADMIN_ORGANIZATION')", coachId, organizationId) > 0;
    }

    public String findHeadCoachIds(String organizationId, String studentId) {
        List<String> result = jdbc.query("SELECT head_coach_ids FROM user_and_coach "
                        + "WHERE organization_id = ? AND user_id = ? AND deleted = 0 AND status IN (0, 1, 4) LIMIT 1",
                new Object[]{organizationId, studentId}, (rs, rowNum) -> rs.getString("head_coach_ids"));
        return result.isEmpty() ? null : result.get(0);
    }

    public List<BookingAppointmentView> findCoachConflicts(
            String organizationId, String coachId, Instant start, Instant end
    ) {
        return jdbc.query(
                "SELECT id, organization_id, user_id, coach_id, course_id, course_name, course_start_time, "
                        + "course_end_time, status, contract_id, NULL AS remaining_class_hours FROM appointment "
                        + "WHERE organization_id = ? AND deleted = 0 AND (coach_id = ? OR temp_coach_id = ?) "
                        + "AND status IN (0, 1, 3, 4, 5) AND course_start_time < ? "
                        + "AND (course_end_time IS NULL OR course_end_time > ? ) ORDER BY course_start_time FOR UPDATE",
                new Object[]{organizationId, coachId, coachId, Timestamp.from(end), Timestamp.from(start)},
                (rs, rowNum) -> mapAppointment(rs));
    }

    public List<LocalDate> findNonBusinessDays(String organizationId, LocalDate from, LocalDate to) {
        List<LocalDate> result = new ArrayList<>();
        jdbc.query("SELECT body FROM system_settings WHERE organization_id = ? AND type = 'Nonbusiness_Day'",
                new Object[]{organizationId}, (rs, rowNum) -> {
                    addDatesFromSettingBody(result, rs.getString("body"));
                    return null;
                });
        result.removeIf(date -> date.isBefore(from) || date.isAfter(to));
        Collections.sort(result);
        return result;
    }

    public List<LocalDate> findCoachVacationDays(String organizationId, String coachId,
                                                  LocalDate from, LocalDate to) {
        List<LocalDate> result = new ArrayList<>();
        jdbc.query("SELECT start_date, end_date FROM vacation_record WHERE organization_id = ? "
                        + "AND coach_id = ? AND status = 0 AND end_date >= ? AND start_date <= ?",
                new Object[]{organizationId, coachId, Date.valueOf(from), Date.valueOf(to)}, (rs, rowNum) -> {
                    Date start = rs.getDate("start_date");
                    Date end = rs.getDate("end_date");
                    if (start != null && end != null) {
                        LocalDate current = start.toLocalDate();
                        while (!current.isAfter(end.toLocalDate())) {
                            if (!current.isBefore(from) && !current.isAfter(to)) result.add(current);
                            current = current.plusDays(1);
                        }
                    }
                    return null;
                });
        Collections.sort(result);
        return result;
    }

    /** 按教练和业务日期串行化创建操作，避免仅依靠普通查询造成并发重复预约。 */
    public void acquireRequestLock(String requestId) {
        Integer locked = jdbc.queryForObject("SELECT GET_LOCK(?, 5)",
                new Object[]{lockName("req", requestId)}, Integer.class);
        if (locked == null || locked != 1) {
            throw new BookingApiException(HttpStatus.CONFLICT, "相同请求正在处理中，请稍后重试");
        }
    }

    public void releaseRequestLock(String requestId) {
        jdbc.queryForObject("SELECT RELEASE_LOCK(?)", new Object[]{lockName("req", requestId)}, Integer.class);
    }

    public void acquireCoachDayLock(String organizationId, String coachId, LocalDate date) {
        String lockName = lockName("coach", organizationId + ":" + coachId + ":" + date);
        Integer locked = jdbc.queryForObject("SELECT GET_LOCK(?, 5)", new Object[]{lockName}, Integer.class);
        if (locked == null || locked != 1) {
            throw new BookingApiException(HttpStatus.CONFLICT, "教练预约资源正被其他请求处理，请稍后重试");
        }
    }

    public void releaseCoachDayLock(String organizationId, String coachId, LocalDate date) {
        String lockName = lockName("coach", organizationId + ":" + coachId + ":" + date);
        jdbc.queryForObject("SELECT RELEASE_LOCK(?)", new Object[]{lockName}, Integer.class);
    }

    @Transactional
    public BookingAppointmentView insertBooking(BookingCreateRequest request, BookingActorData actor,
                                                 ContractRecord contract, CourseRecord course,
                                                 String headCoachIds, BookingConfirmation confirmation,
                                                 int remainingAfterBooking) {
        String appointmentId = UUID.randomUUID().toString().replace("-", "");
        LocalDate startDate = request.getStartTime().atZone(BUSINESS_ZONE).toLocalDate();
        int changed = jdbc.update("UPDATE contract SET remaining_class_hours = ?, version = version + 1 "
                        + "WHERE id = ? AND version = ? AND remaining_class_hours > 0",
                remainingAfterBooking, contract.id, contract.version);
        if (changed != 1) {
            throw new BookingApiException(HttpStatus.CONFLICT, "合同课时已被其他请求消耗，请重新查询");
        }
        jdbc.update("INSERT INTO appointment (id, status, course_name, course_id, course_start_date, "
                        + "course_start_time, course_end_time, create_login_user_id, last_update_login_user_id, "
                        + "head_coach_ids, user_id, coach_id, organization_id, contract_id, mark, amount, deleted) "
                        + "VALUES (?, " + AppointmentStatusCodes.APPOINTMENT_SUCCESS + ", ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                appointmentId, course.name, course.id, Date.valueOf(startDate),
                Timestamp.from(request.getStartTime()), Timestamp.from(request.getEndTime()), actor.userId,
                actor.userId, headCoachIds, request.getStudentId(), request.getCoachId(),
                request.getOrganizationId(), request.getContractId(), request.getMark(),
                String.valueOf(remainingAfterBooking));
        consumeConfirmation(confirmation, actor.requestId);
        jdbc.update("INSERT INTO agent_booking_operation (request_id, appointment_id, organization_id, actor_id) "
                        + "VALUES (?, ?, ?, ?)", actor.requestId, appointmentId, request.getOrganizationId(), actor.userId);
        jdbc.update("INSERT INTO agent_booking_audit (appointment_id, organization_id, action, actor_id, request_id) "
                        + "VALUES (?, ?, 'CREATE_APPOINTMENT', ?, ?)", appointmentId, request.getOrganizationId(), actor.userId,
                actor.requestId);
        String eventKey = "appointment-created:" + appointmentId;
        jdbc.update("INSERT INTO agent_booking_outbox "
                        + "(event_key, event_type, aggregate_id, organization_id, payload) VALUES (?, 'APPOINTMENT_CREATED', ?, ?, ?)",
                eventKey, appointmentId, request.getOrganizationId(),
                "{\"appointmentId\":\"" + escapeJson(appointmentId) + "\",\"studentId\":\""
                        + escapeJson(request.getStudentId()) + "\",\"coachId\":\"" + escapeJson(request.getCoachId()) + "\"}");
        return findByAppointmentId(appointmentId).orElseThrow(
                () -> new IllegalStateException("预约写入后无法读取"));
    }

    private Optional<BookingAppointmentView> findByAppointmentId(String appointmentId) {
        List<BookingAppointmentView> result = jdbc.query(
                "SELECT a.id, a.organization_id, a.user_id, a.coach_id, a.course_id, a.course_name, "
                        + "a.course_start_time, a.course_end_time, a.status, a.contract_id, c.remaining_class_hours "
                        + "FROM appointment a LEFT JOIN contract c ON c.id = a.contract_id WHERE a.id = ? LIMIT 1",
                new Object[]{appointmentId}, (rs, rowNum) -> mapAppointment(rs));
        return result.stream().findFirst();
    }

    private void consumeConfirmation(BookingConfirmation confirmation, String requestId) {
        if (confirmation == null) throw new BookingApiException(HttpStatus.UNAUTHORIZED, "缺少确认凭证");
        try {
            jdbc.update("INSERT INTO agent_booking_confirmation_consumption "
                            + "(jti, confirmation_id, tool_id, action, organization_id, resource, request_id, payload_hash) "
                            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", confirmation.getJti(), confirmation.getConfirmationId(),
                    confirmation.getToolId(), confirmation.getAction(), confirmation.getOrganizationId(),
                    confirmation.getResource(), requestId, confirmation.getPayloadHash());
        } catch (DuplicateKeyException exception) {
            throw new BookingApiException(HttpStatus.CONFLICT, "确认凭证已经被消费");
        }
    }

    private BookingAppointmentView mapAppointment(java.sql.ResultSet rs) throws java.sql.SQLException {
        Timestamp start = rs.getTimestamp("course_start_time");
        Timestamp end = rs.getTimestamp("course_end_time");
        return new BookingAppointmentView(rs.getString("id"), rs.getString("organization_id"),
                rs.getString("user_id"), rs.getString("coach_id"), rs.getString("course_id"),
                rs.getString("course_name"), start == null ? null : start.toInstant(),
                end == null ? null : end.toInstant(), rs.getObject("status", Integer.class),
                rs.getString("contract_id"), rs.getObject("remaining_class_hours", Integer.class));
    }

    private int count(String sql, String... args) {
        Integer count = jdbc.queryForObject(sql, args, Integer.class);
        return count == null ? 0 : count;
    }

    private static void addDatesFromSettingBody(List<LocalDate> result, String body) {
        if (body == null || body.trim().isEmpty()) return;
        String normalized = body.trim().replace("[", "").replace("]", "");
        for (String raw : normalized.split(",")) {
            String value = raw.trim().replace("\"", "");
            try {
                if (value.matches("\\d+")) {
                    result.add(Instant.ofEpochMilli(Long.parseLong(value)).atZone(ZoneOffset.UTC).toLocalDate());
                } else {
                    result.add(LocalDate.parse(value, DateTimeFormatter.ISO_LOCAL_DATE));
                }
            } catch (DateTimeParseException | NumberFormatException ignored) {
                // 旧设置格式无法识别时不擅自禁止预约，最终仍由原业务规则兜底。
            }
        }
    }

    private static String escapeJson(String value) {
        return value == null ? "" : value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    /** MySQL GET_LOCK 名称长度有限，使用固定长度摘要避免用户 ID 组合超出数据库限制。 */
    private static String lockName(String category, String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes("UTF-8"));
            StringBuilder hex = new StringBuilder();
            for (byte item : digest) hex.append(String.format("%02x", item));
            return "fb:" + category + ":" + hex.substring(0, 52);
        } catch (NoSuchAlgorithmException | java.io.UnsupportedEncodingException exception) {
            throw new IllegalStateException("无法生成预约并发锁", exception);
        }
    }

    public static final class ContractRecord {
        public final String id, organizationId, studentId, courseId;
        public final Date startDate, endDate;
        public final int remainingClassHours, status;
        public final long version;
        public ContractRecord(String id, String organizationId, String studentId, String courseId,
                              Date startDate, Date endDate, int remainingClassHours, int status, long version) {
            this.id = id; this.organizationId = organizationId; this.studentId = studentId; this.courseId = courseId;
            this.startDate = startDate; this.endDate = endDate; this.remainingClassHours = remainingClassHours;
            this.status = status; this.version = version;
        }
    }

    public static final class CourseRecord {
        public final String id, name;
        public final int status;
        public CourseRecord(String id, String name, int status) { this.id = id; this.name = name; this.status = status; }
    }

    public static final class BookingActorData {
        public final String userId, requestId;
        public BookingActorData(String userId, String requestId) { this.userId = userId; this.requestId = requestId; }
    }
}

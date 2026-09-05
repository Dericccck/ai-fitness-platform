package com.shuyiwa.fitness.booking.service;

import com.shuyiwa.fitness.booking.api.BookingApiException;
import com.shuyiwa.fitness.booking.api.BookingAppointmentView;
import com.shuyiwa.fitness.booking.api.BookingOperationView;
import com.shuyiwa.fitness.booking.api.BookingCancelRequest;
import com.shuyiwa.fitness.booking.api.BookingCancelledView;
import com.shuyiwa.fitness.booking.api.BookingCreateRequest;
import com.shuyiwa.fitness.booking.api.BookingRescheduleRequest;
import com.shuyiwa.fitness.booking.domain.AppointmentStatusCodes;
import com.shuyiwa.fitness.booking.repository.BookingRepository;
import com.shuyiwa.fitness.booking.security.BookingActor;
import com.shuyiwa.fitness.booking.security.BookingConfirmation;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;

/**
 * 创建预约的业务编排层。
 *
 * <p>这里是最终业务事实边界：即使 Python Agent 已经预检并展示确认卡，服务仍会在
 * 当前事务里重新校验权限、合同、课程、请假、营业日和时间冲突，然后才扣减课时和写预约。</p>
 */
@Service
public class BookingService {
    private static final ZoneId BUSINESS_ZONE = ZoneId.of("Asia/Shanghai");
    private static final int CONTRACT_NORMAL = 1;
    private static final int COURSE_ENABLED = 1;

    private final BookingRepository repository;
    private final Clock clock;

    /**
     * 生产环境使用 UTC 系统时钟；业务请求中的 Instant 再按既有规则转换为机构时区。
     * 将 Clock 注入而不是在方法中直接调用 Instant.now()，可以让跨日期测试固定“当前时刻”，
     * 避免测试数据随日历推进后突然变成过去时间。
     */
    @Autowired
    public BookingService(BookingRepository repository) {
        this(repository, Clock.systemUTC());
    }

    /** 供单元测试注入固定时钟；不改变生产装配方式。 */
    BookingService(BookingRepository repository, Clock clock) {
        this.repository = repository;
        this.clock = clock;
    }

    @Transactional(readOnly = true)
    public BookingOperationView queryOperation(BookingActor actor, String operationId) {
        if (operationId == null || operationId.trim().isEmpty()) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "操作 ID 不能为空");
        }
        BookingRepository.OperationScope scope = repository.findOperationScope(operationId).orElse(null);
        if (scope == null) {
            return new BookingOperationView(operationId, "UNKNOWN", null, null, null);
        }
        if (!actor.canAccessOrganization(scope.organizationId)) {
            throw new BookingApiException(HttpStatus.FORBIDDEN, "操作不在当前主体授权范围内");
        }
        BookingAppointmentView appointment = repository.findByRequestId(operationId).orElse(null);
        if (appointment == null) {
            appointment = repository.findByRescheduleRequestId(operationId).orElse(null);
        }
        if (appointment == null) {
            // 取消操作的结果视图字段不同，但其 request_id 仍然是稳定业务操作 ID；
            // 查询接口只需告诉对账器“已成功”，不泄露额外业务明细。
            BookingCancelledView cancelled =
                    repository.findByCancelRequestId(operationId).orElse(null);
            if (cancelled != null) {
                return new BookingOperationView(operationId, "SUCCEEDED", scope.organizationId,
                        scope.actorId, null);
            }
            // 明确返回 UNKNOWN：未查到不等于原请求没有执行，调用方应继续核实。
            return new BookingOperationView(operationId, "UNKNOWN", scope.organizationId,
                    scope.actorId, null);
        }
        return new BookingOperationView(operationId, "SUCCEEDED", scope.organizationId,
                scope.actorId, appointment);
    }

    @Transactional
    public BookingAppointmentView create(BookingActor actor, BookingCreateRequest request) {
        validateInput(request);
        requireOrganization(actor, request.getOrganizationId());
        requireConfirmation(actor, request);
        requireStudentAccess(actor, request.getOrganizationId(), request.getStudentId());
        if (!repository.isOrganizationMember(request.getOrganizationId(), request.getStudentId())) {
            throw new BookingApiException(HttpStatus.FORBIDDEN, "学员不是当前机构成员");
        }
        if (!repository.isCoachInOrganization(request.getOrganizationId(), request.getCoachId())) {
            throw new BookingApiException(HttpStatus.NOT_FOUND, "教练不属于当前机构");
        }
        // 请求锁解决同一 request_id 的并发重试；教练日期锁解决不同请求的冲突竞态。
        repository.acquireRequestLock(actor.getRequestId());
        boolean coachLocked = false;
        boolean studentLocked = false;
        LocalDate bookingDate = request.getStartTime().atZone(BUSINESS_ZONE).toLocalDate();
        try {
            BookingAppointmentView applied = repository.findByRequestId(actor.getRequestId()).orElse(null);
            if (applied != null) {
                return applied;
            }
            repository.acquireCoachDayLock(request.getOrganizationId(), request.getCoachId(), bookingDate);
            coachLocked = true;
            repository.acquireStudentDayLock(request.getOrganizationId(), request.getStudentId(), bookingDate);
            studentLocked = true;

            BookingRepository.ContractRecord contract = repository.findContractForUpdate(
                    request.getOrganizationId(), request.getStudentId(), request.getContractId());
            if (contract.status != CONTRACT_NORMAL) {
                throw new BookingApiException(HttpStatus.CONFLICT, "合同已失效，无法预约");
            }
            if (contract.startDate == null || contract.endDate == null
                    || bookingDate.isBefore(contract.startDate.toLocalDate())
                    || bookingDate.isAfter(contract.endDate.toLocalDate())) {
                throw new BookingApiException(HttpStatus.CONFLICT, "预约时间不在合同有效期内");
            }
            if (contract.remainingClassHours < 1) {
                throw new BookingApiException(HttpStatus.CONFLICT, "合同剩余课时不足");
            }
            if (!request.getCourseId().equals(contract.courseId)) {
                throw new BookingApiException(HttpStatus.CONFLICT, "预约课程与合同课程不匹配");
            }
            BookingRepository.CourseRecord course = repository.findActiveCourse(
                    request.getOrganizationId(), request.getCourseId()).orElseThrow(
                    () -> new BookingApiException(HttpStatus.NOT_FOUND, "预约课程不存在"));
            if (course.status != COURSE_ENABLED) {
                throw new BookingApiException(HttpStatus.CONFLICT, "预约课程已下线");
            }
            if (!repository.findNonBusinessDays(request.getOrganizationId(), bookingDate, bookingDate).isEmpty()) {
                throw new BookingApiException(HttpStatus.CONFLICT, "机构当天不是营业日");
            }
            if (!repository.findCoachVacationDays(request.getOrganizationId(), request.getCoachId(), bookingDate, bookingDate).isEmpty()) {
                throw new BookingApiException(HttpStatus.CONFLICT, "教练当天正在请假");
            }
            if (!repository.findCoachConflicts(request.getOrganizationId(), request.getCoachId(),
                    request.getStartTime(), request.getEndTime()).isEmpty()) {
                throw new BookingApiException(HttpStatus.CONFLICT, "教练该时间段已有预约");
            }
            if (!repository.findStudentConflicts(request.getOrganizationId(), request.getStudentId(),
                    request.getStartTime(), request.getEndTime(), null).isEmpty()) {
                throw new BookingApiException(HttpStatus.CONFLICT, "学员该时间段已有预约");
            }
            return repository.insertBooking(
                    request,
                    new BookingRepository.BookingActorData(actor.getUserId(), actor.getRequestId()),
                    contract,
                    course,
                    repository.findHeadCoachIds(request.getOrganizationId(), request.getStudentId()),
                    actor.getConfirmation(),
                    contract.remainingClassHours - 1
            );
        } finally {
            if (coachLocked) {
                repository.releaseCoachDayLock(request.getOrganizationId(), request.getCoachId(), bookingDate);
            }
            if (studentLocked) {
                repository.releaseStudentDayLock(request.getOrganizationId(), request.getStudentId(), bookingDate);
            }
            repository.releaseRequestLock(actor.getRequestId());
        }
    }

    @Transactional
    public BookingAppointmentView reschedule(BookingActor actor, BookingRescheduleRequest request) {
        validateRescheduleInput(request);
        requireOrganization(actor, request.getOrganizationId());
        requireRescheduleConfirmation(actor, request);
        repository.acquireRequestLock(actor.getRequestId());
        boolean coachLocked = false;
        boolean studentLocked = false;
        String lockedStudentId = null;
        LocalDate bookingDate = request.getStartTime().atZone(BUSINESS_ZONE).toLocalDate();
        try {
            BookingAppointmentView applied = repository.findByRescheduleRequestId(actor.getRequestId())
                    .orElse(null);
            if (applied != null) return applied;

            BookingAppointmentView current = repository.findAppointmentForUpdate(
                    request.getOrganizationId(), request.getAppointmentId()).orElseThrow(
                    () -> new BookingApiException(HttpStatus.NOT_FOUND, "预约不存在或不属于当前机构"));
            lockedStudentId = current.getUserId();
            if (!AppointmentStatusCodes.canReschedule(current.getStatus())) {
                throw new BookingApiException(HttpStatus.CONFLICT, "当前预约状态不允许改约");
            }
            if (!request.getExpectedStartTime().equals(current.getStartTime())) {
                throw new BookingApiException(HttpStatus.CONFLICT, "预约已被其他操作修改，请重新查询");
            }
            requireStudentAccess(actor, request.getOrganizationId(), current.getUserId());
            if (!repository.isOrganizationMember(request.getOrganizationId(), current.getUserId())) {
                throw new BookingApiException(HttpStatus.FORBIDDEN, "学员不是当前机构成员");
            }
            if (!repository.isCoachInOrganization(request.getOrganizationId(), request.getCoachId())) {
                throw new BookingApiException(HttpStatus.NOT_FOUND, "教练不属于当前机构");
            }

            repository.acquireCoachDayLock(request.getOrganizationId(), request.getCoachId(), bookingDate);
            coachLocked = true;
            repository.acquireStudentDayLock(request.getOrganizationId(), current.getUserId(), bookingDate);
            studentLocked = true;
            BookingRepository.ContractRecord contract = repository.findContractForUpdate(
                    request.getOrganizationId(), current.getUserId(), current.getContractId());
            if (contract.status != CONTRACT_NORMAL) {
                throw new BookingApiException(HttpStatus.CONFLICT, "原预约合同已失效，无法改约");
            }
            if (contract.startDate == null || contract.endDate == null
                    || bookingDate.isBefore(contract.startDate.toLocalDate())
                    || bookingDate.isAfter(contract.endDate.toLocalDate())) {
                throw new BookingApiException(HttpStatus.CONFLICT, "改约时间不在合同有效期内");
            }
            BookingRepository.CourseRecord course = repository.findActiveCourse(
                    request.getOrganizationId(), current.getCourseId()).orElseThrow(
                    () -> new BookingApiException(HttpStatus.NOT_FOUND, "原预约课程不存在"));
            if (course.status != COURSE_ENABLED) {
                throw new BookingApiException(HttpStatus.CONFLICT, "原预约课程已下线");
            }
            if (!repository.findNonBusinessDays(request.getOrganizationId(), bookingDate, bookingDate).isEmpty()) {
                throw new BookingApiException(HttpStatus.CONFLICT, "机构当天不是营业日");
            }
            if (!repository.findCoachVacationDays(request.getOrganizationId(), request.getCoachId(),
                    bookingDate, bookingDate).isEmpty()) {
                throw new BookingApiException(HttpStatus.CONFLICT, "教练当天正在请假");
            }
            if (!repository.findCoachConflicts(request.getOrganizationId(), request.getCoachId(),
                    request.getStartTime(), request.getEndTime(), request.getAppointmentId()).isEmpty()) {
                throw new BookingApiException(HttpStatus.CONFLICT, "教练该时间段已有预约");
            }
            if (!repository.findStudentConflicts(request.getOrganizationId(), current.getUserId(),
                    request.getStartTime(), request.getEndTime(), request.getAppointmentId()).isEmpty()) {
                throw new BookingApiException(HttpStatus.CONFLICT, "学员该时间段已有预约");
            }
            return repository.rescheduleBooking(request,
                    new BookingRepository.BookingActorData(actor.getUserId(), actor.getRequestId()),
                    actor.getConfirmation());
        } finally {
            if (coachLocked) {
                repository.releaseCoachDayLock(request.getOrganizationId(), request.getCoachId(), bookingDate);
            }
            if (studentLocked) {
                repository.releaseStudentDayLock(request.getOrganizationId(), lockedStudentId, bookingDate);
            }
            repository.releaseRequestLock(actor.getRequestId());
        }
    }

    @Transactional
    public BookingCancelledView cancel(BookingActor actor, BookingCancelRequest request) {
        validateCancelInput(request);
        requireOrganization(actor, request.getOrganizationId());
        requireCancelConfirmation(actor, request);
        repository.acquireRequestLock(actor.getRequestId());
        boolean coachLocked = false;
        boolean studentLocked = false;
        String lockedCoachId = null;
        String lockedStudentId = null;
        LocalDate lockedDate = null;
        try {
            BookingCancelledView applied = repository.findByCancelRequestId(actor.getRequestId())
                    .orElse(null);
            if (applied != null) return applied;

            BookingAppointmentView observed = repository.findAppointment(
                    request.getOrganizationId(), request.getAppointmentId()).orElseThrow(
                    () -> new BookingApiException(HttpStatus.NOT_FOUND, "预约不存在或已经取消"));
            if (!AppointmentStatusCodes.canCancel(observed.getStatus())) {
                throw new BookingApiException(HttpStatus.CONFLICT, "当前预约状态不允许取消");
            }
            if (observed.getStartTime() == null || !observed.getStartTime().isAfter(Instant.now(clock))) {
                throw new BookingApiException(HttpStatus.CONFLICT, "课程已经开始，无法取消预约");
            }
            if (!request.getExpectedStartTime().equals(observed.getStartTime())) {
                throw new BookingApiException(HttpStatus.CONFLICT, "预约已被其他操作修改，请重新查询");
            }
            requireStudentAccess(actor, request.getOrganizationId(), observed.getUserId());
            if (!repository.isOrganizationMember(request.getOrganizationId(), observed.getUserId())) {
                throw new BookingApiException(HttpStatus.FORBIDDEN, "学员不是当前机构成员");
            }

            LocalDate bookingDate = observed.getStartTime().atZone(BUSINESS_ZONE).toLocalDate();
            lockedCoachId = observed.getCoachId();
            lockedStudentId = observed.getUserId();
            lockedDate = bookingDate;
            repository.acquireCoachDayLock(request.getOrganizationId(), lockedCoachId, lockedDate);
            coachLocked = true;
            repository.acquireStudentDayLock(request.getOrganizationId(), observed.getUserId(), lockedDate);
            studentLocked = true;
            // 创建/改约先拿教练日期锁再锁业务行；取消也遵循同样顺序，避免锁顺序反转造成死锁。
            BookingAppointmentView current = repository.findAppointmentForUpdate(
                    request.getOrganizationId(), request.getAppointmentId()).orElseThrow(
                    () -> new BookingApiException(HttpStatus.NOT_FOUND, "预约不存在或已经取消"));
            if (!AppointmentStatusCodes.canCancel(current.getStatus())
                    || current.getStartTime() == null
                    || !request.getExpectedStartTime().equals(current.getStartTime())
                    || !java.util.Objects.equals(observed.getCoachId(), current.getCoachId())
                    || !observed.getStartTime().atZone(BUSINESS_ZONE).toLocalDate()
                    .equals(current.getStartTime().atZone(BUSINESS_ZONE).toLocalDate())) {
                throw new BookingApiException(HttpStatus.CONFLICT, "预约已被其他操作修改，请重新查询");
            }
            BookingRepository.ContractRecord contract = repository.findContractForUpdate(
                    request.getOrganizationId(), current.getUserId(), current.getContractId());
            return repository.cancelBooking(request,
                    new BookingRepository.BookingActorData(actor.getUserId(), actor.getRequestId()),
                    contract, actor.getConfirmation());
        } finally {
            // 取消会释放教练时间段，同时恢复课时；需要与创建/改约共用同一业务日期锁。
            if (coachLocked && lockedCoachId != null && lockedDate != null) {
                repository.releaseCoachDayLock(request.getOrganizationId(), lockedCoachId, lockedDate);
            }
            if (studentLocked && lockedDate != null) {
                repository.releaseStudentDayLock(request.getOrganizationId(), lockedStudentId, lockedDate);
            }
            repository.releaseRequestLock(actor.getRequestId());
        }
    }

    private void validateInput(BookingCreateRequest request) {
        if (request == null || blank(request.getOrganizationId()) || blank(request.getStudentId())
                || blank(request.getContractId()) || blank(request.getCoachId()) || blank(request.getCourseId())
                || request.getStartTime() == null || request.getEndTime() == null) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "预约参数不完整");
        }
        if (!request.getEndTime().isAfter(request.getStartTime())) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "预约结束时间必须晚于开始时间");
        }
        if (request.getEndTime().isAfter(request.getStartTime().plusSeconds(8 * 3600L))) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "单次预约时长不能超过 8 小时");
        }
        if (request.getStartTime().isBefore(Instant.now(clock))) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "预约开始时间不能早于当前时间");
        }
    }

    private void validateRescheduleInput(BookingRescheduleRequest request) {
        if (request == null || blank(request.getOrganizationId()) || blank(request.getAppointmentId())
                || blank(request.getCoachId()) || request.getExpectedStartTime() == null
                || request.getStartTime() == null || request.getEndTime() == null) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "改约参数不完整");
        }
        if (!request.getEndTime().isAfter(request.getStartTime())) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "预约结束时间必须晚于开始时间");
        }
        if (request.getEndTime().isAfter(request.getStartTime().plusSeconds(8 * 3600L))) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "单次预约时长不能超过 8 小时");
        }
        if (request.getStartTime().isBefore(Instant.now(clock))) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "预约开始时间不能早于当前时间");
        }
    }

    private void validateCancelInput(BookingCancelRequest request) {
        if (request == null || blank(request.getOrganizationId()) || blank(request.getAppointmentId())
                || request.getExpectedStartTime() == null) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "取消预约参数不完整");
        }
    }

    private void requireStudentAccess(BookingActor actor, String organizationId, String studentId) {
        if (actor.isAdministrator()) return;
        if (actor.hasRole(BookingActor.STUDENT) && !actor.getUserId().equals(studentId)) {
            throw new BookingApiException(HttpStatus.FORBIDDEN, "学员只能为本人预约");
        }
        if (actor.hasRole(BookingActor.COACH)
                && !actor.getUserId().equals(studentId)
                && !repository.isCoachForStudent(organizationId, actor.getUserId(), studentId)) {
            throw new BookingApiException(HttpStatus.FORBIDDEN, "教练不能为未分配的学员预约");
        }
        if (!actor.hasRole(BookingActor.STUDENT) && !actor.hasRole(BookingActor.COACH)) {
            throw new BookingApiException(HttpStatus.FORBIDDEN, "当前主体没有创建预约权限");
        }
    }

    private void requireConfirmation(BookingActor actor, BookingCreateRequest request) {
        BookingConfirmation confirmation = actor.getConfirmation();
        if (confirmation == null) {
            throw new BookingApiException(HttpStatus.UNAUTHORIZED, "缺少预约确认凭证");
        }
        if (!"fitness.booking.create.v1".equals(confirmation.getToolId())
                || !"CREATE_APPOINTMENT".equals(confirmation.getAction())
                || !request.getOrganizationId().equals(confirmation.getOrganizationId())
                || !request.getContractId().equals(confirmation.getResource())
                || !actor.canAccessOrganization(confirmation.getOrganizationId())
                || !confirmation.getPayloadHash().matches("[0-9a-fA-F]{64}")) {
            throw new BookingApiException(HttpStatus.FORBIDDEN, "预约确认凭证范围与请求不匹配");
        }
    }

    private void requireRescheduleConfirmation(BookingActor actor, BookingRescheduleRequest request) {
        BookingConfirmation confirmation = actor.getConfirmation();
        if (confirmation == null) {
            throw new BookingApiException(HttpStatus.UNAUTHORIZED, "缺少改约确认凭证");
        }
        if (!"fitness.booking.reschedule.v1".equals(confirmation.getToolId())
                || !"RESCHEDULE_APPOINTMENT".equals(confirmation.getAction())
                || !request.getOrganizationId().equals(confirmation.getOrganizationId())
                || !request.getAppointmentId().equals(confirmation.getResource())
                || !actor.canAccessOrganization(confirmation.getOrganizationId())
                || !confirmation.getPayloadHash().matches("[0-9a-fA-F]{64}")) {
            throw new BookingApiException(HttpStatus.FORBIDDEN, "改约确认凭证范围与请求不匹配");
        }
    }

    private void requireCancelConfirmation(BookingActor actor, BookingCancelRequest request) {
        BookingConfirmation confirmation = actor.getConfirmation();
        if (confirmation == null) {
            throw new BookingApiException(HttpStatus.UNAUTHORIZED, "缺少取消预约确认凭证");
        }
        if (!"fitness.booking.cancel.v1".equals(confirmation.getToolId())
                || !"CANCEL_APPOINTMENT".equals(confirmation.getAction())
                || !request.getOrganizationId().equals(confirmation.getOrganizationId())
                || !request.getAppointmentId().equals(confirmation.getResource())
                || !actor.canAccessOrganization(confirmation.getOrganizationId())
                || !confirmation.getPayloadHash().matches("[0-9a-fA-F]{64}")) {
            throw new BookingApiException(HttpStatus.FORBIDDEN, "取消预约确认凭证范围与请求不匹配");
        }
    }

    private void requireOrganization(BookingActor actor, String organizationId) {
        if (!actor.canAccessOrganization(organizationId)) {
            throw new BookingApiException(HttpStatus.FORBIDDEN, "机构不在当前主体授权范围内");
        }
    }

    private static boolean blank(String value) { return value == null || value.trim().isEmpty(); }
}

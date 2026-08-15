package com.shuyiwa.fitness.booking.service;

import com.shuyiwa.fitness.booking.api.BookingApiException;
import com.shuyiwa.fitness.booking.api.BookingAppointmentView;
import com.shuyiwa.fitness.booking.api.BookingCreateRequest;
import com.shuyiwa.fitness.booking.repository.BookingRepository;
import com.shuyiwa.fitness.booking.security.BookingActor;
import com.shuyiwa.fitness.booking.security.BookingConfirmation;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

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

    public BookingService(BookingRepository repository) { this.repository = repository; }

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
        LocalDate bookingDate = request.getStartTime().atZone(BUSINESS_ZONE).toLocalDate();
        try {
            BookingAppointmentView applied = repository.findByRequestId(actor.getRequestId()).orElse(null);
            if (applied != null) {
                return applied;
            }
            repository.acquireCoachDayLock(request.getOrganizationId(), request.getCoachId(), bookingDate);
            coachLocked = true;

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
        if (request.getStartTime().isBefore(Instant.now())) {
            throw new BookingApiException(HttpStatus.BAD_REQUEST, "预约开始时间不能早于当前时间");
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

    private void requireOrganization(BookingActor actor, String organizationId) {
        if (!actor.canAccessOrganization(organizationId)) {
            throw new BookingApiException(HttpStatus.FORBIDDEN, "机构不在当前主体授权范围内");
        }
    }

    private static boolean blank(String value) { return value == null || value.trim().isEmpty(); }
}

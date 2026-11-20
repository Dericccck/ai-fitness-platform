package com.shuyiwa.fitness.booking.service;

import com.shuyiwa.fitness.booking.api.BookingAppointmentView;
import com.shuyiwa.fitness.booking.api.BookingCancelRequest;
import com.shuyiwa.fitness.booking.api.BookingCancelledView;
import com.shuyiwa.fitness.booking.api.BookingCreateRequest;
import com.shuyiwa.fitness.booking.api.BookingRescheduleRequest;
import com.shuyiwa.fitness.booking.api.BookingOperationView;
import com.shuyiwa.fitness.booking.repository.BookingRepository;
import com.shuyiwa.fitness.booking.security.BookingActor;
import com.shuyiwa.fitness.booking.security.BookingConfirmation;
import org.junit.Test;

import java.sql.Date;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.Collections;
import java.util.HashSet;
import java.util.Optional;

import static org.junit.Assert.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.never;

public class BookingServiceTest {
    private final BookingRepository repository = mock(BookingRepository.class);
    // 固定在测试数据之前，避免真实系统日期推进后 2026-08-20/21 的夹具失效。
    private final BookingService service = new BookingService(repository,
            Clock.fixed(Instant.parse("2026-08-19T00:00:00Z"), ZoneOffset.UTC));

    @Test
    public void operationQueryReturnsImmutableOrganizationAndActorScope() {
        when(repository.findOperationScope("operation-1")).thenReturn(Optional.of(
                new BookingRepository.OperationScope("org-1", "student-1")));
        BookingAppointmentView appointment = new BookingAppointmentView(
                "appointment-1", "org-1", "student-1", "coach-1", "course-1", "力量训练",
                Instant.parse("2026-08-20T10:00:00Z"), Instant.parse("2026-08-20T11:00:00Z"),
                1, "contract-1", 1);
        when(repository.findByRequestId("operation-1")).thenReturn(Optional.of(appointment));

        BookingOperationView result = service.queryOperation(studentActor(), "operation-1");

        assertEquals("SUCCEEDED", result.getStatus());
        assertEquals("org-1", result.getOrganizationId());
        assertEquals("student-1", result.getActorId());
    }

    @Test
    public void studentCanCreateOwnBookingAfterFinalRuleCheck() {
        BookingCreateRequest request = request();
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);
        when(repository.isCoachInOrganization("org-1", "coach-1")).thenReturn(true);
        when(repository.findByRequestId("request-1")).thenReturn(Optional.empty());
        when(repository.findContractForUpdate("org-1", "student-1", "contract-1"))
                .thenReturn(contract(2));
        when(repository.findActiveCourse("org-1", "course-1"))
                .thenReturn(Optional.of(new BookingRepository.CourseRecord("course-1", "力量训练", 1)));
        when(repository.findNonBusinessDays(eq("org-1"), any(LocalDate.class), any(LocalDate.class)))
                .thenReturn(Collections.emptyList());
        when(repository.findCoachVacationDays(eq("org-1"), eq("coach-1"), any(LocalDate.class), any(LocalDate.class)))
                .thenReturn(Collections.emptyList());
        when(repository.findCoachConflicts("org-1", "coach-1", request.getStartTime(), request.getEndTime()))
                .thenReturn(Collections.emptyList());
        when(repository.findHeadCoachIds("org-1", "student-1")).thenReturn("coach-1");
        BookingAppointmentView expected = new BookingAppointmentView(
                "appointment-1", "org-1", "student-1", "coach-1", "course-1", "力量训练",
                request.getStartTime(), request.getEndTime(), 1, "contract-1", 1
        );
        when(repository.insertBooking(
                org.mockito.ArgumentMatchers.eq(request), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.eq("coach-1"), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.eq(1)
        )).thenReturn(expected);

        BookingAppointmentView actual = service.create(studentActor(), request);

        assertEquals("appointment-1", actual.getId());
        verify(repository).acquireRequestLock("request-1");
        verify(repository).acquireCoachDayLock("org-1", "coach-1", LocalDate.of(2026, 8, 20));
        verify(repository).releaseCoachDayLock("org-1", "coach-1", LocalDate.of(2026, 8, 20));
        verify(repository).releaseRequestLock("request-1");
    }

    @Test
    public void missingConfirmationIsRejectedBeforeDatabaseWrite() {
        BookingActor actor = new BookingActor("student-1", set(BookingActor.STUDENT), set("org-1"),
                "request-1", null);

        try {
            service.create(actor, request());
        } catch (com.shuyiwa.fitness.booking.api.BookingApiException expected) {
            assertEquals(401, expected.getStatus().value());
            return;
        }
        throw new AssertionError("missing confirmation must be rejected");
    }

    @Test
    public void expiredContractIsRejectedBeforeInsertAndReleasesLocks() {
        setupCreateChecks();
        when(repository.findContractForUpdate("org-1", "student-1", "contract-1"))
                .thenReturn(new BookingRepository.ContractRecord("contract-1", "org-1", "student-1",
                        "course-1", Date.valueOf("2026-01-01"), Date.valueOf("2026-12-31"), 2, 0, 0));
        assertCreateRejected(request(), 409, "合同已失效，无法预约");
        assertNoCreateAndLocksReleased();
    }

    @Test
    public void exhaustedContractIsRejectedBeforeInsertAndReleasesLocks() {
        setupCreateChecks();
        when(repository.findContractForUpdate("org-1", "student-1", "contract-1"))
                .thenReturn(contract(0));
        assertCreateRejected(request(), 409, "合同剩余课时不足");
        assertNoCreateAndLocksReleased();
    }

    @Test
    public void studentCannotBookForAnotherStudent() {
        BookingCreateRequest request = request();
        request.setStudentId("other-student");
        assertCreateRejected(request, 403, "学员只能为本人预约");
        verify(repository, never()).acquireRequestLock(any());
        verify(repository, never()).insertBooking(any(), any(), any(), any(), any(), any(), anyInt());
    }

    @Test
    public void existingCoachAppointmentRejectsNewBookingWithoutWrite() {
        BookingCreateRequest request = request();
        setupCreateChecks();
        when(repository.findCoachConflicts("org-1", "coach-1", request.getStartTime(), request.getEndTime()))
                .thenReturn(Collections.singletonList(mock(BookingAppointmentView.class)));
        assertCreateRejected(request, 409, "教练该时间段已有预约");
        assertNoCreateAndLocksReleased();
    }

    @Test
    public void existingStudentAppointmentRejectsNewBookingWithoutWrite() {
        BookingCreateRequest request = request();
        setupCreateChecks();
        when(repository.findStudentConflicts("org-1", "student-1", request.getStartTime(), request.getEndTime(), null))
                .thenReturn(Collections.singletonList(mock(BookingAppointmentView.class)));
        assertCreateRejected(request, 409, "学员该时间段已有预约");
        assertNoCreateAndLocksReleased();
    }

    private void setupCreateChecks() {
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);
        when(repository.isCoachInOrganization("org-1", "coach-1")).thenReturn(true);
        when(repository.findByRequestId("request-1")).thenReturn(Optional.empty());
        when(repository.findContractForUpdate("org-1", "student-1", "contract-1"))
                .thenReturn(contract(2));
        when(repository.findActiveCourse("org-1", "course-1"))
                .thenReturn(Optional.of(new BookingRepository.CourseRecord("course-1", "力量训练", 1)));
    }

    @Test
    public void crossMidnightCreateLocksBothDaysAndChecksBothCalendars() {
        setupCreateChecks();
        BookingCreateRequest request = request();
        request.setStartTime(Instant.parse("2026-08-20T15:30:00Z"));
        request.setEndTime(Instant.parse("2026-08-20T16:30:00Z"));
        service.create(studentActor(), request);
        LocalDate first = LocalDate.of(2026, 8, 20), second = first.plusDays(1);
        verify(repository).acquireCoachDayLock("org-1", "coach-1", first);
        verify(repository).acquireCoachDayLock("org-1", "coach-1", second);
        verify(repository).acquireStudentDayLock("org-1", "student-1", first);
        verify(repository).acquireStudentDayLock("org-1", "student-1", second);
        verify(repository).findNonBusinessDays("org-1", first, second);
        verify(repository).findCoachVacationDays("org-1", "coach-1", first, second);
    }

    @Test
    public void bookingEndingAtMidnightDoesNotLockNextDay() {
        setupCreateChecks();
        BookingCreateRequest request = request();
        request.setStartTime(Instant.parse("2026-08-20T15:30:00Z"));
        request.setEndTime(Instant.parse("2026-08-20T16:00:00Z"));
        service.create(studentActor(), request);
        verify(repository).acquireCoachDayLock("org-1", "coach-1", LocalDate.of(2026, 8, 20));
        verify(repository, never()).acquireCoachDayLock("org-1", "coach-1", LocalDate.of(2026, 8, 21));
    }

    @Test
    public void crossMidnightBookingRejectsSecondDayClosure() {
        setupCreateChecks();
        BookingCreateRequest request = request();
        request.setStartTime(Instant.parse("2026-08-20T15:30:00Z"));
        request.setEndTime(Instant.parse("2026-08-20T16:30:00Z"));
        when(repository.findNonBusinessDays("org-1", LocalDate.of(2026, 8, 20),
                LocalDate.of(2026, 8, 21))).thenReturn(Collections.singletonList(LocalDate.of(2026, 8, 21)));
        assertCreateRejected(request, 409, "机构当天不是营业日");
        verify(repository, never()).insertBooking(any(), any(), any(), any(), any(), any(), anyInt());
    }

    @Test
    public void crossMidnightBookingRejectsContractExpiredOnSecondDay() {
        setupCreateChecks();
        BookingCreateRequest request = request();
        request.setStartTime(Instant.parse("2026-12-31T15:30:00Z"));
        request.setEndTime(Instant.parse("2026-12-31T16:30:00Z"));
        assertCreateRejected(request, 409, "预约时间不在合同有效期内");
        verify(repository, never()).insertBooking(any(), any(), any(), any(), any(), any(), anyInt());
    }

    @Test
    public void crossMidnightRescheduleLocksBothDays() {
        BookingRescheduleRequest request = rescheduleRequest();
        request.setStartTime(Instant.parse("2026-08-21T15:30:00Z"));
        request.setEndTime(Instant.parse("2026-08-21T16:30:00Z"));
        BookingAppointmentView current = new BookingAppointmentView(
                "appointment-1", "org-1", "student-1", "coach-1", "course-1", "力量训练",
                request.getExpectedStartTime(), Instant.parse("2026-08-20T11:00:00Z"),
                1, "contract-1", 0);
        when(repository.findByRescheduleRequestId("request-2")).thenReturn(Optional.empty());
        when(repository.findAppointmentForUpdate("org-1", "appointment-1")).thenReturn(Optional.of(current));
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);
        when(repository.isCoachInOrganization("org-1", "coach-2")).thenReturn(true);
        when(repository.findContractForUpdate("org-1", "student-1", "contract-1")).thenReturn(contract(0));
        when(repository.findActiveCourse("org-1", "course-1"))
                .thenReturn(Optional.of(new BookingRepository.CourseRecord("course-1", "力量训练", 1)));
        service.reschedule(rescheduleActor(), request);
        LocalDate first = LocalDate.of(2026, 8, 21), second = first.plusDays(1);
        verify(repository).acquireCoachDayLock("org-1", "coach-2", first);
        verify(repository).acquireCoachDayLock("org-1", "coach-2", second);
        verify(repository).acquireStudentDayLock("org-1", "student-1", first);
        verify(repository).acquireStudentDayLock("org-1", "student-1", second);
        verify(repository).findNonBusinessDays("org-1", first, second);
        verify(repository).findCoachVacationDays("org-1", "coach-2", first, second);
    }

    private void assertCreateRejected(BookingCreateRequest request, int status, String reason) {
        try {
            service.create(studentActor(), request);
            throw new AssertionError("invalid booking was accepted");
        } catch (com.shuyiwa.fitness.booking.api.BookingApiException expected) {
            assertEquals(status, expected.getStatus().value());
            assertEquals(reason, expected.getMessage());
        }
    }

    private void assertNoCreateAndLocksReleased() {
        verify(repository, never()).insertBooking(any(), any(), any(), any(), any(), any(), anyInt());
        verify(repository).releaseStudentDayLock("org-1", "student-1", LocalDate.of(2026, 8, 20));
        verify(repository).releaseCoachDayLock("org-1", "coach-1", LocalDate.of(2026, 8, 20));
        verify(repository).releaseRequestLock("request-1");
    }

    @Test
    public void studentCanRescheduleOwnBookingWithoutConsumingAnotherClassHour() {
        BookingRescheduleRequest request = rescheduleRequest();
        BookingAppointmentView current = new BookingAppointmentView(
                "appointment-1", "org-1", "student-1", "coach-1", "course-1", "力量训练",
                request.getExpectedStartTime(), Instant.parse("2026-08-20T11:00:00Z"),
                1, "contract-1", 0
        );
        BookingAppointmentView expected = new BookingAppointmentView(
                "appointment-1", "org-1", "student-1", "coach-2", "course-1", "力量训练",
                request.getStartTime(), request.getEndTime(), 1, "contract-1", 0
        );
        when(repository.findByRescheduleRequestId("request-2")).thenReturn(Optional.empty());
        when(repository.findAppointmentForUpdate("org-1", "appointment-1"))
                .thenReturn(Optional.of(current));
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);
        when(repository.isCoachInOrganization("org-1", "coach-2")).thenReturn(true);
        when(repository.findContractForUpdate("org-1", "student-1", "contract-1"))
                .thenReturn(contract(0));
        when(repository.findActiveCourse("org-1", "course-1"))
                .thenReturn(Optional.of(new BookingRepository.CourseRecord("course-1", "力量训练", 1)));
        when(repository.findNonBusinessDays(eq("org-1"), any(LocalDate.class), any(LocalDate.class)))
                .thenReturn(Collections.emptyList());
        when(repository.findCoachVacationDays(eq("org-1"), eq("coach-2"), any(LocalDate.class), any(LocalDate.class)))
                .thenReturn(Collections.emptyList());
        when(repository.findCoachConflicts("org-1", "coach-2", request.getStartTime(), request.getEndTime(),
                "appointment-1")).thenReturn(Collections.emptyList());
        when(repository.rescheduleBooking(eq(request), any(), any())).thenReturn(expected);

        BookingAppointmentView actual = service.reschedule(rescheduleActor(), request);

        assertEquals("coach-2", actual.getCoachId());
        assertEquals(0, actual.getRemainingClassHours().intValue());
        verify(repository).acquireCoachDayLock("org-1", "coach-2", LocalDate.of(2026, 8, 21));
        verify(repository).releaseCoachDayLock("org-1", "coach-2", LocalDate.of(2026, 8, 21));
        verify(repository).releaseRequestLock("request-2");
    }

    @Test
    public void rescheduleWithoutConfirmationIsRejectedBeforeDatabaseWrite() {
        BookingActor actor = new BookingActor("student-1", set(BookingActor.STUDENT), set("org-1"),
                "request-2", null);

        try {
            service.reschedule(actor, rescheduleRequest());
        } catch (com.shuyiwa.fitness.booking.api.BookingApiException expected) {
            assertEquals(401, expected.getStatus().value());
            return;
        }
        throw new AssertionError("missing reschedule confirmation must be rejected");
    }

    @Test
    public void studentCanCancelFutureBookingAndRestoreOneClassHour() {
        BookingCancelRequest request = cancelRequest();
        BookingAppointmentView current = new BookingAppointmentView(
                "appointment-1", "org-1", "student-1", "coach-1", "course-1", "力量训练",
                request.getExpectedStartTime(), Instant.parse("2026-08-20T11:00:00Z"),
                1, "contract-1", 0
        );
        BookingCancelledView expected = new BookingCancelledView(
                "appointment-1", "org-1", "student-1", "coach-1", "course-1", "力量训练",
                request.getExpectedStartTime(), Instant.parse("2026-08-20T11:00:00Z"),
                1, "contract-1", 1
        );
        when(repository.findByCancelRequestId("request-3")).thenReturn(Optional.empty());
        when(repository.findAppointment("org-1", "appointment-1"))
                .thenReturn(Optional.of(current));
        when(repository.findAppointmentForUpdate("org-1", "appointment-1"))
                .thenReturn(Optional.of(current));
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);
        when(repository.findContractForUpdate("org-1", "student-1", "contract-1"))
                .thenReturn(contract(0));
        when(repository.cancelBooking(eq(request), any(), any(), any())).thenReturn(expected);

        BookingCancelledView actual = service.cancel(cancelActor(), request);

        assertEquals("appointment-1", actual.getId());
        assertEquals(1, actual.getRemainingClassHours().intValue());
        verify(repository).acquireCoachDayLock("org-1", "coach-1", LocalDate.of(2026, 8, 20));
        verify(repository).releaseCoachDayLock("org-1", "coach-1", LocalDate.of(2026, 8, 20));
        verify(repository).releaseRequestLock("request-3");
    }

    @Test
    public void cancelWithoutConfirmationIsRejectedBeforeDatabaseWrite() {
        BookingActor actor = new BookingActor("student-1", set(BookingActor.STUDENT), set("org-1"),
                "request-3", null);

        try {
            service.cancel(actor, cancelRequest());
        } catch (com.shuyiwa.fitness.booking.api.BookingApiException expected) {
            assertEquals(401, expected.getStatus().value());
            return;
        }
        throw new AssertionError("missing cancel confirmation must be rejected");
    }

    @Test
    public void legacyOperationQueryRejectsAnotherActorInSameOrganization() {
        when(repository.findOperationScope("operation-1")).thenReturn(Optional.of(
                new BookingRepository.OperationScope("org-1", "other-student")));
        try { service.queryOperation(studentActor(), "operation-1"); throw new AssertionError("cross-actor query"); }
        catch (com.shuyiwa.fitness.booking.api.BookingApiException expected) {
            assertEquals(403, expected.getStatus().value());
        }
    }

    @Test
    public void requestBindingConflictStopsAllThreeWriteEntrypointsBeforeIdempotentReuse() {
        org.mockito.Mockito.doThrow(new com.shuyiwa.fitness.booking.api.BookingApiException(
                org.springframework.http.HttpStatus.CONFLICT, "binding mismatch"))
                .when(repository).assertRequestBinding(any(), any(), any());
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);
        when(repository.isCoachInOrganization("org-1", "coach-1")).thenReturn(true);
        for (int index = 0; index < 3; index++) {
            try {
                if (index == 0) service.create(studentActor(), request());
                else if (index == 1) service.reschedule(rescheduleActor(), rescheduleRequest());
                else service.cancel(cancelActor(), cancelRequest());
                throw new AssertionError("conflicting operation was accepted");
            } catch (com.shuyiwa.fitness.booking.api.BookingApiException expected) {
                assertEquals(409, expected.getStatus().value());
            }
        }
        org.mockito.Mockito.verify(repository, org.mockito.Mockito.never()).findByRequestId(any());
        org.mockito.Mockito.verify(repository, org.mockito.Mockito.never()).findByRescheduleRequestId(any());
        org.mockito.Mockito.verify(repository, org.mockito.Mockito.never()).findByCancelRequestId(any());
    }

    private static BookingActor studentActor() {
        return new BookingActor("student-1", set(BookingActor.STUDENT), set("org-1"), "request-1",
                new BookingConfirmation("confirmation-1", "jti-1", "fitness.booking.create.v1",
                        "CREATE_APPOINTMENT", "org-1", "contract-1", repeat("a", 64)));
    }

    private static BookingCreateRequest request() {
        BookingCreateRequest request = new BookingCreateRequest();
        request.setOrganizationId("org-1");
        request.setStudentId("student-1");
        request.setContractId("contract-1");
        request.setCoachId("coach-1");
        request.setCourseId("course-1");
        request.setStartTime(Instant.parse("2026-08-20T10:00:00Z"));
        request.setEndTime(Instant.parse("2026-08-20T11:00:00Z"));
        return request;
    }

    private static BookingRescheduleRequest rescheduleRequest() {
        BookingRescheduleRequest request = new BookingRescheduleRequest();
        request.setOrganizationId("org-1");
        request.setAppointmentId("appointment-1");
        request.setCoachId("coach-2");
        request.setExpectedStartTime(Instant.parse("2026-08-20T10:00:00Z"));
        request.setStartTime(Instant.parse("2026-08-21T10:00:00Z"));
        request.setEndTime(Instant.parse("2026-08-21T11:00:00Z"));
        return request;
    }

    private static BookingCancelRequest cancelRequest() {
        BookingCancelRequest request = new BookingCancelRequest();
        request.setOrganizationId("org-1");
        request.setAppointmentId("appointment-1");
        request.setExpectedStartTime(Instant.parse("2026-08-20T10:00:00Z"));
        return request;
    }

    private static BookingActor rescheduleActor() {
        return new BookingActor("student-1", set(BookingActor.STUDENT), set("org-1"), "request-2",
                new BookingConfirmation("confirmation-2", "jti-2", "fitness.booking.reschedule.v1",
                        "RESCHEDULE_APPOINTMENT", "org-1", "appointment-1", repeat("b", 64)));
    }

    private static BookingActor cancelActor() {
        return new BookingActor("student-1", set(BookingActor.STUDENT), set("org-1"), "request-3",
                new BookingConfirmation("confirmation-3", "jti-3", "fitness.booking.cancel.v1",
                        "CANCEL_APPOINTMENT", "org-1", "appointment-1", repeat("c", 64)));
    }

    private static BookingRepository.ContractRecord contract(int remaining) {
        return new BookingRepository.ContractRecord("contract-1", "org-1", "student-1", "course-1",
                Date.valueOf("2026-01-01"), Date.valueOf("2026-12-31"), remaining, 1, 0);
    }

    private static String repeat(String value, int count) {
        StringBuilder result = new StringBuilder();
        for (int index = 0; index < count; index++) result.append(value);
        return result.toString();
    }

    private static HashSet<String> set(String value) {
        HashSet<String> result = new HashSet<>(); result.add(value); return result;
    }
}

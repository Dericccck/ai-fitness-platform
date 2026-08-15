package com.shuyiwa.fitness.booking.service;

import com.shuyiwa.fitness.booking.api.BookingAppointmentView;
import com.shuyiwa.fitness.booking.api.BookingCreateRequest;
import com.shuyiwa.fitness.booking.api.BookingRescheduleRequest;
import com.shuyiwa.fitness.booking.repository.BookingRepository;
import com.shuyiwa.fitness.booking.security.BookingActor;
import com.shuyiwa.fitness.booking.security.BookingConfirmation;
import org.junit.Test;

import java.sql.Date;
import java.time.Instant;
import java.time.LocalDate;
import java.util.Collections;
import java.util.HashSet;
import java.util.Optional;

import static org.junit.Assert.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

public class BookingServiceTest {
    private final BookingRepository repository = mock(BookingRepository.class);
    private final BookingService service = new BookingService(repository);

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

    private static BookingActor rescheduleActor() {
        return new BookingActor("student-1", set(BookingActor.STUDENT), set("org-1"), "request-2",
                new BookingConfirmation("confirmation-2", "jti-2", "fitness.booking.reschedule.v1",
                        "RESCHEDULE_APPOINTMENT", "org-1", "appointment-1", repeat("b", 64)));
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

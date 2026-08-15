package com.shuyiwa.fitness.booking.api;

import com.shuyiwa.fitness.booking.security.BookingActor;
import com.shuyiwa.fitness.booking.service.BookingService;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 内部预约写接口；只接受 Gateway 注入的主体和确认声明。 */
@RestController
@RequestMapping("/internal/booking/v1")
public class BookingController {
    private final BookingService service;

    public BookingController(BookingService service) { this.service = service; }

    @PostMapping("/appointments")
    public BookingAppointmentView create(BookingActor actor, @RequestBody BookingCreateRequest request) {
        return service.create(actor, request);
    }

    @PostMapping("/appointments/{appointmentId}/reschedule")
    public BookingAppointmentView reschedule(BookingActor actor,
                                              @PathVariable String appointmentId,
                                              @RequestBody BookingRescheduleRequest request) {
        // 路径 ID 和请求体 ID 必须一致，避免代理层或客户端拼接出两个不同资源。
        request.setAppointmentId(appointmentId);
        return service.reschedule(actor, request);
    }

    @PostMapping("/appointments/{appointmentId}/cancel")
    public BookingCancelledView cancel(BookingActor actor,
                                       @PathVariable String appointmentId,
                                       @RequestBody BookingCancelRequest request) {
        // 路径 ID 是唯一资源来源，避免请求体中的预约 ID 与 URL 不一致。
        request.setAppointmentId(appointmentId);
        return service.cancel(actor, request);
    }
}

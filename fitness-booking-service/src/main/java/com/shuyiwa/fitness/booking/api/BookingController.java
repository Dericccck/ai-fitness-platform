package com.shuyiwa.fitness.booking.api;

import com.shuyiwa.fitness.booking.security.BookingActor;
import com.shuyiwa.fitness.booking.service.BookingService;
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
}

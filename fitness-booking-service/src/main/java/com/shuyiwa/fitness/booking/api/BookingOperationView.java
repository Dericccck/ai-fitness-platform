package com.shuyiwa.fitness.booking.api;

/** 预约业务操作查询结果；UNKNOWN 不代表失败，原请求可能仍在途。 */
public final class BookingOperationView {
    private final String operationId;
    private final String status;
    private final BookingAppointmentView appointment;

    public BookingOperationView(String operationId, String status, BookingAppointmentView appointment) {
        this.operationId = operationId;
        this.status = status;
        this.appointment = appointment;
    }

    public String getOperationId() { return operationId; }
    public String getStatus() { return status; }
    public BookingAppointmentView getAppointment() { return appointment; }
}

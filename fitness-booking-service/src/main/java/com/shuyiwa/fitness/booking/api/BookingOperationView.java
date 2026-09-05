package com.shuyiwa.fitness.booking.api;

/** 预约业务操作查询结果；UNKNOWN 不代表失败，原请求可能仍在途。 */
public final class BookingOperationView {
    private final String operationId;
    private final String status;
    private final String organizationId;
    private final String actorId;
    private final BookingAppointmentView appointment;

    public BookingOperationView(String operationId, String status, String organizationId,
                                String actorId, BookingAppointmentView appointment) {
        this.operationId = operationId;
        this.status = status;
        this.organizationId = organizationId;
        this.actorId = actorId;
        this.appointment = appointment;
    }

    public String getOperationId() { return operationId; }
    public String getStatus() { return status; }
    public String getOrganizationId() { return organizationId; }
    public String getActorId() { return actorId; }
    public BookingAppointmentView getAppointment() { return appointment; }
}

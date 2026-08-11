package com.shuyiwa.fitness.backend.domain.dict;

public enum AppointmentStatus {
    APPOINTING(0,"预约中"),
    APPOINTMENT_SUCCESS(1,"预约成功"),
    APPOINTMENT_FAIL(2,"预约失败"),
    APPOINTMENT_CHANGEING(3,"改课中"),
    WAITINGFOR_FINISHCLASS(4,"待核销"),
    FINISHING(5,"完成课程中"),
    FINISH_SUCCESS(6,"已完成"),
    FINISH_FAIL(7,"核销失败");

    private int status;
    private String name;

    AppointmentStatus(int status){
        this.status = status;
    }

    AppointmentStatus(int status,String name){
        this.status = status;
        this.name = name;
    }

    public static String getAppointmentName(int status){
        for(AppointmentStatus ele:values()){
            return ele.getName();
        }
        return "";

    }

    public int getStatus() {
        return status;
    }

    public void setStatus(int status) {
        this.status = status;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }
}

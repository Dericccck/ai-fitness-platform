package com.shuyiwa.fitness.backend.domain.dict;

public enum VacationStatus {
    Vacation_NOTCANCEL(0,"未取消"),
    Vacation_CANCEL(1,"已取消");

    private int status;
    private String name;

    VacationStatus(int status){
        this.status = status;
    }

    VacationStatus(int status, String name){
        this.status = status;
        this.name = name;
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

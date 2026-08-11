package com.shuyiwa.fitness.backend.domain.dict;

public enum StoreDataDetailsStatus {
    NEW_CUSTOMER(1,"新客"),
    MODIFY_CONTRACT(2,"修改合约"),
    REFUND(3,"退款"),
    OTHER(4,"其他");

    private int status;
    private String name;

    StoreDataDetailsStatus(int status){
        this.status = status;
    }

    StoreDataDetailsStatus(int status, String name){
        this.status = status;
        this.name = name;
    }

    public static String getContractName(int status){
        for(StoreDataDetailsStatus ele:values()){
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

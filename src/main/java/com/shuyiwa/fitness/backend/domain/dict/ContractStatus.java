package com.shuyiwa.fitness.backend.domain.dict;

public enum ContractStatus {
    Contract_NORMAL(1,"正常"),
    Contract_ABNORMALEND(2,"异常结束"),
    Contract_NORMALEND(3,"正常结束"),
    Contract_NOTCONSUMED(4,"课程未消耗关闭合约"),
    Contract_CONSUMED(5,"课程至少消耗了1关闭合约");

    private int status;
    private String name;

    ContractStatus(int status){
        this.status = status;
    }

    ContractStatus(int status, String name){
        this.status = status;
        this.name = name;
    }

    public static String getContractName(int status){
        for(ContractStatus ele:values()){
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

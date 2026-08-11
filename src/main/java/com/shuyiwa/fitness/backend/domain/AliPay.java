package com.shuyiwa.fitness.backend.domain;

import javax.persistence.Column;
import javax.persistence.Embeddable;
import javax.persistence.Temporal;
import javax.persistence.TemporalType;
import javax.validation.constraints.Digits;
import java.math.BigDecimal;
import java.util.Date;

@Embeddable
public class AliPay {
    @Column
    private boolean aliLaunched;
    //该交易在支付宝系统中的交易流水号。最长64位。
    @Column(length = 64)
    private String aliTradeNo;

    @Column
    private String aliPayUrl;
    @Column
    private String aliForm;

    @Column
    @Digits(integer = 11, fraction = 0)
    private BigDecimal aliTotalAmount;

    @Temporal(TemporalType.TIMESTAMP)
    @Column
    private Date aliPayExpiredTime;

    public boolean isAliLaunched() {
        return aliLaunched;
    }

    public void setAliLaunched(boolean aliLaunched) {
        this.aliLaunched = aliLaunched;
    }

    public String getAliPayUrl() {
        return aliPayUrl;
    }

    public void setAliPayUrl(String aliPayUrl) {
        this.aliPayUrl = aliPayUrl;
    }

    public Date getAliPayExpiredTime() {
        return aliPayExpiredTime;
    }

    public void setAliPayExpiredTime(Date aliPayExpiredTime) {
        this.aliPayExpiredTime = aliPayExpiredTime;
    }

    public String getAliTradeNo() {
        return aliTradeNo;
    }

    public void setAliTradeNo(String aliTradeNo) {
        this.aliTradeNo = aliTradeNo;
    }

    public String getAliForm() {
        return aliForm;
    }

    public void setAliForm(String aliForm) {
        this.aliForm = aliForm;
    }

    public BigDecimal getAliTotalAmount() {
        return aliTotalAmount;
    }

    public void setAliTotalAmount(BigDecimal aliTotalAmount) {
        this.aliTotalAmount = aliTotalAmount;
    }
}

package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonIdentityInfo;
import com.fasterxml.jackson.annotation.ObjectIdGenerators;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.util.Date;

/**
 * 短信模版
 */
@JsonIdentityInfo(generator = ObjectIdGenerators.PropertyGenerator.class, property = "id", resolver = EntityIdResolver.class, scope = SmsHistory.class)
@Entity
@Table(indexes = {@Index(columnList = "dupCheckCode,result")})
public class SmsHistory {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Column
    private String phone;
    @Column
    private String template;
    @Column
    private String param;

    /**
     * 不能重复发送的短信用于监测是否重复的标识
     */
    @Column(length = 100)
    private String dupCheckCode;

    @Column
    private String response;

    @Column(nullable = false, length = 20)
    @Enumerated(EnumType.STRING)
    private SmsResult result;
    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getPhone() {
        return phone;
    }

    public void setPhone(String phone) {
        this.phone = phone;
    }

    public String getTemplate() {
        return template;
    }

    public void setTemplate(String template) {
        this.template = template;
    }

    public String getResponse() {
        return response;
    }

    public void setResponse(String response) {
        this.response = response;
    }

    public SmsResult getResult() {
        return result;
    }

    public void setResult(SmsResult result) {
        this.result = result;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public String getParam() {
        return param;
    }

    public void setParam(String param) {
        this.param = param;
    }

    public String getDupCheckCode() {
        return dupCheckCode;
    }

    public void setDupCheckCode(String dupCheckCode) {
        this.dupCheckCode = dupCheckCode;
    }

    public enum SmsResult {
        SUCCESS, FAIL, EXCEPTION, IGNORE
    }
}


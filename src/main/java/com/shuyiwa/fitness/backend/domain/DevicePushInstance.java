package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonIdentityReference;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.util.Date;

@Entity
@Table(indexes = {@Index(columnList = "app,createTime,status,device_push_id"), @Index(columnList = "login_user_id,device_push_id,app", unique = true)})
public class DevicePushInstance {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @ManyToOne
    @JsonIdentityReference(alwaysAsId = true)
    @JoinColumn
    private LoginUser loginUser;

    @ManyToOne
    @JsonIdentityReference(alwaysAsId = true)
    @JoinColumn
    private DevicePush devicePush;

    @Column(nullable = false, length = 20)
    private String app;

    @Column
    private String requestId;
    @Column
    private String messageId;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date updateTime;


    @Column(nullable = false, length = 20)
    @Enumerated(EnumType.STRING)
    private Status status = Status.INIT;

    @Version
    private long version = 0l;


    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public LoginUser getLoginUser() {
        return loginUser;
    }

    public void setLoginUser(LoginUser loginUser) {
        this.loginUser = loginUser;
    }

    public DevicePush getDevicePush() {
        return devicePush;
    }

    public void setDevicePush(DevicePush devicePush) {
        this.devicePush = devicePush;
    }

    public String getApp() {
        return app;
    }

    public void setApp(String app) {
        this.app = app;
    }

    public String getRequestId() {
        return requestId;
    }

    public void setRequestId(String requestId) {
        this.requestId = requestId;
    }

    public String getMessageId() {
        return messageId;
    }

    public void setMessageId(String messageId) {
        this.messageId = messageId;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Date getUpdateTime() {
        return updateTime;
    }

    public void setUpdateTime(Date updateTime) {
        this.updateTime = updateTime;
    }

    public Status getStatus() {
        return status;
    }

    public void setStatus(Status status) {
        this.status = status;
    }

    public long getVersion() {
        return version;
    }

    public void setVersion(long version) {
        this.version = version;
    }

    public enum Status {
        INIT, DONE, IGNORE, FAILED
    }
}

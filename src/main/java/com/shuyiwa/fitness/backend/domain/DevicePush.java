package com.shuyiwa.fitness.backend.domain;

import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.util.Date;

@Entity
public class DevicePush {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Column
    private String title;
    @Column
    private String body;

    @Column
    @Temporal(TemporalType.TIMESTAMP)
    private Date schedule;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date updateTime;


    @Column
    private String prepareLogic;
    @Column
    private String checkLogic;
    @Column
    private String nextLogic;
    @Column
    private boolean storeOffline;
    @Column
    @Temporal(TemporalType.TIMESTAMP)
    private Date expireTime;

    public String getNextLogic() {
        return nextLogic;
    }

    public void setNextLogic(String nextLogic) {
        this.nextLogic = nextLogic;
    }

    @Version
    private long version = 0l;

    @Column(nullable = false, length = 20)
    @Enumerated(EnumType.STRING)
    private Status status = Status.INIT;

    public Status getStatus() {
        return status;
    }

    public void setStatus(Status status) {
        this.status = status;
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getBody() {
        return body;
    }

    public void setBody(String body) {
        this.body = body;
    }

    public Date getSchedule() {
        return schedule;
    }

    public void setSchedule(Date schedule) {
        this.schedule = schedule;
    }

    public long getVersion() {
        return version;
    }

    public void setVersion(long version) {
        this.version = version;
    }

    public String getPrepareLogic() {
        return prepareLogic;
    }

    public void setPrepareLogic(String prepareLogic) {
        this.prepareLogic = prepareLogic;
    }

    public String getCheckLogic() {
        return checkLogic;
    }

    public void setCheckLogic(String checkLogic) {
        this.checkLogic = checkLogic;
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

    public boolean isStoreOffline() {
        return storeOffline;
    }

    public void setStoreOffline(boolean storeOffline) {
        this.storeOffline = storeOffline;
    }

    public Date getExpireTime() {
        return expireTime;
    }

    public void setExpireTime(Date expireTime) {
        this.expireTime = expireTime;
    }

    public enum Status {
        INIT, READY, DONE
    }
}

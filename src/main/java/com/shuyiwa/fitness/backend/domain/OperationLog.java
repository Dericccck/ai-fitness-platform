package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonIdentityReference;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.util.Date;

@Entity
@Table(indexes = {@Index(columnList = "eventType,entityName,entityId,createTime"), @Index(columnList = "createTime"), @Index(columnList = "createTime,entityName"), @Index(columnList = "entityId")})
public class OperationLog {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;


    @JsonIdentityReference(alwaysAsId = true)
    @ManyToOne
    private LoginUser loginUser;

    @Column(length = 50)
    private String eventType;

    @Column(length = 50)
    private String entityName;

    @Column(length = 32)
    private String entityId;

    @Lob
    @Column
    private String stateMap;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

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

    public String getEntityName() {
        return entityName;
    }

    public void setEntityName(String entityName) {
        this.entityName = entityName;
    }

    public String getStateMap() {
        return stateMap;
    }

    public void setStateMap(String stateMap) {
        this.stateMap = stateMap;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public String getEntityId() {
        return entityId;
    }

    public void setEntityId(String entityId) {
        this.entityId = entityId;
    }

    public String getEventType() {
        return eventType;
    }

    public void setEventType(String eventType) {
        this.eventType = eventType;
    }
}

package com.shuyiwa.fitness.backend.domain;


import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import org.hibernate.annotations.GenericGenerator;
import org.hibernate.annotations.Type;

import javax.persistence.*;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;
import java.util.Objects;

@Entity
@Table(indexes = {@Index(columnList = "createTime")})
public class UserCoachHistory {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Column(length = 32,nullable = false)
    private String userId;

    @Column
    @Type(type = "text")
    private String headCoachId;

    @Column(length = 255,nullable = false)
    private String coachId;

    @Column(length = 32,nullable = false)
    private String organizationId;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false,updatable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Transient
    private Map<String, Object> properties = new HashMap<>();

    @JsonAnyGetter
    public Map<String, Object> getProperties() {
        return properties;
    }

    @JsonAnySetter
    public void setProperty(String name, Object value) {
        properties.put(name, value);
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getUserId() {
        return userId;
    }

    public String getCoachId() {
        return coachId;
    }

    public void setCoachId(String coachId) {
        this.coachId = coachId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public String getHeadCoachId() {
        return headCoachId;
    }

    public void setHeadCoachId(String headCoachId) {
        this.headCoachId = headCoachId;
    }

    public String getOrganizationId() {
        return organizationId;
    }

    public void setOrganizationId(String organizationId) {
        this.organizationId = organizationId;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public void setProperties(Map<String, Object> properties) {
        this.properties = properties;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof UserCoachHistory)) return false;
        UserCoachHistory that = (UserCoachHistory) o;
        return Objects.equals(getId(), that.getId()) && Objects.equals(getUserId(), that.getUserId()) && Objects.equals(getHeadCoachId(), that.getHeadCoachId()) && Objects.equals(getOrganizationId(), that.getOrganizationId()) && Objects.equals(getCreateTime(), that.getCreateTime()) && Objects.equals(getProperties(), that.getProperties());
    }

    @Override
    public int hashCode() {
        return Objects.hash(getId(), getUserId(), getHeadCoachId(), getOrganizationId(), getCreateTime(), getProperties());
    }
}


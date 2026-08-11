package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonIdentityReference;
import org.hibernate.annotations.GenericGenerator;
import org.hibernate.annotations.Type;

import javax.persistence.*;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;

/**
 * 用户和教练关系
 */
@Entity
@Table(indexes = {
        @Index(columnList = "user_id,organization_id", unique = true),
        @Index(columnList = "coach_id,organization_id")
})
public class UserAndCoach {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Column
    @Temporal(TemporalType.TIMESTAMP)
    private Date lastClassTime;

    //状态0待确认，1已确认,2更换教练中（废弃）,3解约中（废弃）,4拒绝邀请或已解约
    @Column(length = 4)
    private int status = 0;

    /**
     * 创建者
     */
    @ManyToOne
    @JoinColumn(name = "create_login_user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser createLoginUser;

    /**
     * 用户
     */
    @ManyToOne
    @JoinColumn(name = "user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser user;

    /**
     * 机构
     */
    @ManyToOne
    @JoinColumn(name = "organization_id")
    @JsonIdentityReference(alwaysAsId = true)
    private Organization organization;

    /**
     * 教练
     */
    @ManyToOne
    @JoinColumn(name = "coach_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser coach;

    /**
     * 主教练id
     */
    @Column
    @Type(type = "text")
    private String headCoachIds;


    public int getStatus() {
        return status;
    }

    @Column(length = 4)
    private Integer classHour = 0;

    @Column
    private Integer amount = 0;


    @Column
    private String remarkUserName;

    //用户状态，1:活跃用户，0：非活跃用户
    @Column(length = 4)
    private Integer userStatus = 1;

    @Column
    private boolean deleted = false;

    @Version
    private Integer version=0;

    @Transient
    private Map<String, Object> properties = new HashMap<>();

    public String getHeadCoachIds() {
        return headCoachIds;
    }

    public void setHeadCoachIds(String headCoachIds) {
        this.headCoachIds = headCoachIds;
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Date getLastClassTime() {
        return lastClassTime;
    }

    public void setLastClassTime(Date lastClassTime) {
        this.lastClassTime = lastClassTime;
    }

    public LoginUser getCreateLoginUser() {
        return createLoginUser;
    }

    public void setCreateLoginUser(LoginUser createLoginUser) {
        this.createLoginUser = createLoginUser;
    }

    public LoginUser getUser() {
        return user;
    }

    public void setUser(LoginUser user) {
        this.user = user;
    }

    public Organization getOrganization() {
        return organization;
    }

    public void setOrganization(Organization organization) {
        this.organization = organization;
    }

    public LoginUser getCoach() {
        return coach;
    }

    public void setCoach(LoginUser coach) {
        this.coach = coach;
    }

    public int getClassHour() {
        return classHour;
    }

    public void setClassHour(int classHour) {
        this.classHour = classHour;
    }

    @JsonAnyGetter
    public Map<String, Object> getProperties() {
        return properties;
    }

    @JsonAnySetter
    public void setProperty(String name, Object value) {
        properties.put(name, value);
    }

    public void setStatus(int status) {
        this.status = status;
    }

    public String getRemarkUserName() {
        return remarkUserName;
    }

    public void setRemarkUserName(String remarkUserName) {
        this.remarkUserName = remarkUserName;
    }

    public int getUserStatus() {
        return userStatus;
    }

    public void setUserStatus(int userStatus) {
        this.userStatus = userStatus;
    }

    public void setProperties(Map<String, Object> properties) {
        this.properties = properties;
    }

    public boolean isDeleted() {
        return deleted;
    }

    public void setDeleted(boolean deleted) {
        this.deleted = deleted;
    }

    public int getVersion() {
        return version;
    }

    public void setVersion(int version) {
        this.version = version;
    }

    public int getAmount() {
        return amount;
    }

    public void setAmount(int amount) {
        this.amount = amount;
    }
}

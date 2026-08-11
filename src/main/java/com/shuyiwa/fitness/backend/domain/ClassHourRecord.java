package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonIdentityReference;
import com.shuyiwa.fitness.backend.domain.dict.NewsType;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;

@Entity
@Table(indexes = {
        @Index(columnList = "login_user_id,organization_id"),
})
public class ClassHourRecord {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

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
    @JoinColumn(name = "login_user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser loginUser;

    /**
     * 金额
     */
    @Column(name = "amount")
    private Integer amount;

    /**
     * 教练
     */
    @ManyToOne
    @JoinColumn(name = "coach_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser coach;

    @Column(length = 8)
    private int classHour = 0;

    @ManyToOne
    @JoinColumn(name = "organization_id")
    @JsonIdentityReference(alwaysAsId = true)
    private Organization organization;


    @Column
    private  Integer times;


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

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public LoginUser getCreateLoginUser() {
        return createLoginUser;
    }

    public void setCreateLoginUser(LoginUser createLoginUser) {
        this.createLoginUser = createLoginUser;
    }

    public LoginUser getLoginUser() {
        return loginUser;
    }

    public void setLoginUser(LoginUser loginUser) {
        this.loginUser = loginUser;
    }

    public int getAmount() {
        return amount;
    }

    public void setAmount(int amount) {
        this.amount = amount;
    }

    public int getClassHour() {
        return classHour;
    }

    public void setClassHour(int classHour) {
        this.classHour = classHour;
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

    public Integer getTimes() {
        return times;
    }

    public void setTimes(Integer times) {
        this.times = times;
    }
}


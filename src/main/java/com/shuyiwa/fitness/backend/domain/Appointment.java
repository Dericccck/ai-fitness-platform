package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonIdentityReference;
import org.hibernate.annotations.GenericGenerator;
import org.hibernate.annotations.Type;

import javax.persistence.*;
import java.io.Serializable;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;
import java.util.Objects;

@Entity
@Table(indexes = {
        @Index(columnList = "user_id,organization_id"),
        @Index(columnList = "coach_id,organization_id")
})
public class Appointment implements Serializable {


    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    //0：预约中，1：预约成功，2：拒绝预约，3：改课中（拒绝后/确认后改为预约成功）4，待核销，5：核销中，6：已核销， 7：拒绝核销
    @Column(length = 16)
    private int status = 0;

    @Column
    private String courseName;

    @Column
    private String courseId;

    @Column
    private String payType;

    @Column
    private String amount;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    /**
     * 开始日期
     */
    @Column
    @Temporal(TemporalType.DATE)
    private Date courseStartDate;

    /**
     * 开始时间
     */
    @Column
    private Date courseStartTime;


    @Column
    private Date courseEndTime;

    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    @Temporal(TemporalType.TIMESTAMP)
    private Date lastUpdateTime;

    /**
     * 创建者
     */
    @ManyToOne
    @JoinColumn(name = "create_login_user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser createLoginUser;

    /**
     * 修改人
     */
    @ManyToOne
    @JoinColumn(name = "last_update_login_user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser lastUpdateLoginUser;

    /**
     * 主教练id
     */
    @Column
    @Type(type = "text")
    private String headCoachIds;


    /**
     * 用户
     */
    @ManyToOne
    @JoinColumn(name = "user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser user;

    /**
     * 上课教练
     */
    @ManyToOne
    @JoinColumn(name = "coach_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser coach;


    /**
     * 代课教练  （目前没用）
     */
    @ManyToOne
    @JoinColumn(name = "temp_coach_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser tempCoach;

    /**
     * 机构
     */
    @ManyToOne
    @JoinColumn(name = "organization_id")
    @JsonIdentityReference(alwaysAsId = true)
    private Organization organization;

    /**
     * 合同id
     */
    @Column(length = 32)
    private String contractId;

    /**
     * 0 后台     1 小程序
     */
    @Column
    private Integer mark;

    @Transient
    private Contract contract;

    @Column
    private boolean deleted = false;

    /**
     * 确认完成课程时间
     */
    @Column
    @Temporal(TemporalType.TIMESTAMP)
    private Date confirmTime;

    @Column
    private String reamrk;

    @Column(length = 32)
    private String lastApplyUserId;

    @Transient
    private Map<String, Object> properties = new HashMap<>();

    public Contract getContract() {
        return contract;
    }

    public void setContract(Contract contract) {
        this.contract = contract;
    }


    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public int getStatus() {
        return status;
    }

    public Date getCourseStartDate() {
        return courseStartDate;
    }

    public void setCourseStartDate(Date courseStartDate) {
        this.courseStartDate = courseStartDate;
    }

    public LoginUser getLastUpdateLoginUser() {
        return lastUpdateLoginUser;
    }

    public void setLastUpdateLoginUser(LoginUser lastUpdateLoginUser) {
        this.lastUpdateLoginUser = lastUpdateLoginUser;
    }

    public String getContractId() {
        return contractId;
    }

    public void setContractId(String contractId) {
        this.contractId = contractId;
    }

    public void setStatus(int status) {
        this.status = status;
    }

    public String getCourseName() {
        return courseName;
    }

    public void setCourseName(String courseName) {
        this.courseName = courseName;
    }

    public String getHeadCoachIds() {
        return headCoachIds;
    }

    public void setHeadCoachIds(String headCoachIds) {
        this.headCoachIds = headCoachIds;
    }

    public Date getConfirmTime() {
        return confirmTime;
    }

    public void setConfirmTime(Date confirmTime) {
        this.confirmTime = confirmTime;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Date getCourseStartTime() {
        return courseStartTime;
    }

    public void setCourseStartTime(Date courseStartTime) {
        this.courseStartTime = courseStartTime;
    }

    public Date getCourseEndTime() {
        return courseEndTime;
    }

    public void setCourseEndTime(Date courseEndTime) {
        this.courseEndTime = courseEndTime;
    }

    public Date getLastUpdateTime() {
        return lastUpdateTime;
    }

    public void setLastUpdateTime(Date lastUpdateTime) {
        this.lastUpdateTime = lastUpdateTime;
    }

    public LoginUser getCreateLoginUser() {
        return createLoginUser;
    }

    public void setCreateLoginUser(LoginUser createLoginUser) {
        this.createLoginUser = createLoginUser;
    }

    public Integer getMark() {
        return mark;
    }

    public void setMark(Integer mark) {
        this.mark = mark;
    }

    public LoginUser getUser() {
        return user;
    }

    public void setUser(LoginUser user) {
        this.user = user;
    }

    public LoginUser getCoach() {
        return coach;
    }

    public void setCoach(LoginUser coach) {
        this.coach = coach;
    }

    public LoginUser getTempCoach() {
        return tempCoach;
    }

    public void setTempCoach(LoginUser tempCoach) {
        this.tempCoach = tempCoach;
    }

    @JsonAnyGetter
    public Map<String, Object> getProperties() {
        return properties;
    }

    @JsonAnySetter
    public void setProperty(String name, Object value) {
        properties.put(name, value);
    }

    public Organization getOrganization() {
        return organization;
    }

    public void setOrganization(Organization organization) {
        this.organization = organization;
    }

    public boolean isDeleted() {
        return deleted;
    }

    public void setDeleted(boolean deleted) {
        this.deleted = deleted;
    }

    public String getCourseId() {
        return courseId;
    }

    public void setCourseId(String courseId) {
        this.courseId = courseId;
    }

    public String getPayType() {
        return payType;
    }

    public void setPayType(String payType) {
        this.payType = payType;
    }

    public String getAmount() {
        return amount;
    }

    public void setAmount(String amount) {
        this.amount = amount;
    }

    public String getReamrk() {
        return reamrk;
    }

    public void setReamrk(String reamrk) {
        this.reamrk = reamrk;
    }

    public String getLastApplyUserId() {
        return lastApplyUserId;
    }

    public void setLastApplyUserId(String lastApplyUserId) {
        this.lastApplyUserId = lastApplyUserId;
    }

    public Appointment() {
    }

    @Override
    public String toString() {
        return "Appointment{" +
                "id='" + id + '\'' +
                ", status=" + status +
                ", courseName='" + courseName + '\'' +
                ", courseId='" + courseId + '\'' +
                ", payType='" + payType + '\'' +
                ", amount='" + amount + '\'' +
                ", createTime=" + createTime +
                ", courseStartTime=" + courseStartTime +
                ", courseEndTime=" + courseEndTime +
                ", lastUpdateTime=" + lastUpdateTime +
                ", createLoginUser=" + createLoginUser +
                ", user=" + user +
                ", coach=" + coach +
                ", organization=" + organization +
                ", contractId='" + contractId + '\'' +
                ", contract=" + contract +
                ", deleted=" + deleted +
                ", reamrk='" + reamrk + '\'' +
                ", lastApplyUserId='" + lastApplyUserId + '\'' +
                ", properties=" + properties +
                '}';
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        Appointment that = (Appointment) o;
        return status == that.status &&
                deleted == that.deleted &&
                Objects.equals(id, that.id) &&
                Objects.equals(courseName, that.courseName) &&
                Objects.equals(courseId, that.courseId) &&
                Objects.equals(payType, that.payType) &&
                Objects.equals(amount, that.amount) &&
                Objects.equals(createTime, that.createTime) &&
                Objects.equals(courseStartTime, that.courseStartTime) &&
                Objects.equals(courseEndTime, that.courseEndTime) &&
                Objects.equals(lastUpdateTime, that.lastUpdateTime) &&
                Objects.equals(createLoginUser, that.createLoginUser) &&
                Objects.equals(user, that.user) &&
                Objects.equals(coach, that.coach) &&
                Objects.equals(organization, that.organization);
    }

    @Override
    public int hashCode() {
        return Objects.hash(id, status, courseName, courseId, payType, amount, createTime, courseStartTime, courseEndTime, lastUpdateTime, createLoginUser, user, coach, organization, deleted);
    }
}

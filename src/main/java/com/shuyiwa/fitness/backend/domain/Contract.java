package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonIdentityReference;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.io.Serializable;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;
import java.util.Objects;

/**
 * 合同
 */
@Entity
@Table(indexes={
        @Index(columnList = "numberId", unique = true)
})
public class Contract implements Serializable {

    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    /**
     * 机构
     */
    @ManyToOne
    @JoinColumn(name = "organization_id")
    @JsonIdentityReference(alwaysAsId = true)
    private Organization organization;

    /**
     * 合约编号
     */
    @Column
    private String numberId;


    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    /**
     * 合同生效时间
     */
    @Column
    @Temporal(TemporalType.DATE)
    private Date contractCreateTime;

    /**
     * 合同截止时间
     */
    @Column
    @Temporal(TemporalType.DATE)
    private Date contractEndTime;


    /**
     * 修改时间
     */
    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date updateTime;

    /**
     * 创建人
     */
    @Column(length = 36)
    private String Creator;

    /**
     * 签约人
     */
    @Column(length = 255)
    private String signatoryId;

    /**
     * 课程id
     */
    @Column(length = 32)
    private String courseId;
    /**
     * 总金额
     */
    @Column
    private Integer totalAmount;

    /**
     * 退款金额
     */
    @Column
    private Integer refundAmount = 0;

    /**
     * 新客
     */
    @Column
    private Boolean newCustomer = false;

    /**
     * 课时
     */
    @Column
    private Integer classHour = 0;

    /**
     * 余课
     */
    @Column
    private Integer remainingClassHours = 0;

    /**
     * 合约状态   1.正常   2.异常结束   3.正常结束   4.关闭的合约1-课程未消耗   5.关闭的合约2-课程至少消耗了1
     */
    @Column
    private Integer status;

    @Column(length = 1024)
    private String introduction;

    /**
     * 用户
     */
    @ManyToOne
    @JoinColumn(name = "user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser user;

    @Version
    private Long version=0l;

    /**
     * 教练核销课时
     */
    @Column
    private Integer finishClassHour = 0;


    /**
     * 课程名称
     */
    @Transient
    private String courseName;

    @Column
    private Integer deleted = 0;

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

    public void setProperties(Map<String, Object> properties) {
        this.properties = properties;
    }

    public Contract() {
    }

    public Contract(String id, Organization organization, String numberId, Date createTime, Date contractCreateTime, Date contractEndTime, Date updateTime, String creator, String signatoryId, String courseId, Integer totalAmount, Integer classHour, Integer remainingClassHours, Integer status, LoginUser user, String courseName, Map<String, Object> properties, String introduction) {
        this.id = id;
        this.organization = organization;
        this.numberId = numberId;
        this.createTime = createTime;
        this.contractCreateTime = contractCreateTime;
        this.contractEndTime = contractEndTime;
        this.updateTime = updateTime;
        Creator = creator;
        this.signatoryId = signatoryId;
        this.courseId = courseId;
        this.totalAmount = totalAmount;
        this.classHour = classHour;
        this.remainingClassHours = remainingClassHours;
        this.status = status;
        this.user = user;
        this.courseName = courseName;
        this.properties = properties;
        this.introduction = introduction;
    }

    public Integer getRefundAmount() {
        return refundAmount;
    }

    public void setRefundAmount(Integer refundAmount) {
        this.refundAmount = refundAmount;
    }

    public Boolean getNewCustomer() {
        return newCustomer;
    }

    public void setNewCustomer(Boolean newCustomer) {
        this.newCustomer = newCustomer;
    }

    public Integer getFinishClassHour() {
        return finishClassHour;
    }

    public void setFinishClassHour(Integer cancelClassHour) {
        this.finishClassHour = cancelClassHour;
    }

    public String getIntroduction() {
        return introduction;
    }

    public void setIntroduction(String introduction) {
        this.introduction = introduction;
    }

    public Integer isDeleted() {
        return deleted;
    }

    public void setDeleted(Integer deleted) {
        this.deleted = deleted;
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public Organization getOrganization() {
        return organization;
    }

    public void setOrganization(Organization organization) {
        this.organization = organization;
    }

    public String getNumberId() {
        return numberId;
    }

    public void setNumberId(String numberId) {
        this.numberId = numberId;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Date getContractCreateTime() {
        return contractCreateTime;
    }

    public void setContractCreateTime(Date contractCreateTime) {
        this.contractCreateTime = contractCreateTime;
    }

    public Date getContractEndTime() {
        return contractEndTime;
    }

    public void setContractEndTime(Date contractEndTime) {
        this.contractEndTime = contractEndTime;
    }

    public Date getUpdateTime() {
        return updateTime;
    }

    public void setUpdateTime(Date updateTime) {
        this.updateTime = updateTime;
    }

    public String getCreator() {
        return Creator;
    }

    public void setCreator(String creator) {
        Creator = creator;
    }

    public String getSignatoryId() {
        return signatoryId;
    }

    public void setSignatoryId(String signatoryId) {
        this.signatoryId = signatoryId;
    }

    public String getCourseId() {
        return courseId;
    }

    public void setCourseId(String courseId) {
        this.courseId = courseId;
    }

    public Integer getTotalAmount() {
        return totalAmount;
    }

    public void setTotalAmount(Integer totalAmount) {
        this.totalAmount = totalAmount;
    }

    public Integer getClassHour() {
        return classHour;
    }

    public void setClassHour(Integer classHour) {
        this.classHour = classHour;
    }

    public Integer getRemainingClassHours() {
        return remainingClassHours;
    }

    public void setRemainingClassHours(Integer remainingClassHours) {
        this.remainingClassHours = remainingClassHours;
    }

    public Integer getStatus() {
        return status;
    }

    public void setStatus(Integer status) {
        this.status = status;
    }

    public LoginUser getUser() {
        return user;
    }

    public void setUser(LoginUser user) {
        this.user = user;
    }

    public String getCourseName() {
        return courseName;
    }

    public void setCourseName(String courseName) {
        this.courseName = courseName;
    }

    public Long getVersion() {
        return version;
    }

    public void setVersion(Long version) {
        this.version = version;
    }

    @Override
    public String toString() {
        return "Contract{" +
                "id='" + id + '\'' +
                ", organization=" + organization +
                ", numberId='" + numberId + '\'' +
                ", createTime=" + createTime +
                ", contractCreateTime=" + contractCreateTime +
                ", contractEndTime=" + contractEndTime +
                ", updateTime=" + updateTime +
                ", Creator='" + Creator + '\'' +
                ", signatoryId='" + signatoryId + '\'' +
                ", courseId='" + courseId + '\'' +
                ", totalAmount=" + totalAmount +
                ", classHour=" + classHour +
                ", remainingClassHours=" + remainingClassHours +
                ", status=" + status +
                ", introduction='" + introduction + '\'' +
                ", user=" + user +
                ", version=" + version +
                ", courseName='" + courseName + '\'' +
                ", properties=" + properties +
                '}';
    }
}

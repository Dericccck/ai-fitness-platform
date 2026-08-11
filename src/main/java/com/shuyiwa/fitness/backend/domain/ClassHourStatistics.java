package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonIdentityReference;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.util.Date;

/**
 * 统计教练每日上课数
 */
@Entity
@Table(indexes = {@Index(columnList = "coachId,statisticsDate", unique = true)})
public class ClassHourStatistics {

    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    //教练
    @Column
    private String coachId;

    //创建日期
    @Column
    @Temporal(TemporalType.DATE)
    private Date statisticsDate;

    //统计时间
    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    //上课数量
    @Column
    private Integer classNumber;


    //机构
    @Column
    private String organizationId;


    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getCoachId() {
        return coachId;
    }

    public void setCoachId(String coachId) {
        this.coachId = coachId;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Integer getClassNumber() {
        return classNumber;
    }

    public void setClassNumber(Integer classNumber) {
        this.classNumber = classNumber;
    }

    public String getOrganizationId() {
        return organizationId;
    }

    public void setOrganizationId(String organizationId) {
        this.organizationId = organizationId;
    }

    public Date getStatisticsDate() {
        return statisticsDate;
    }

    public void setStatisticsDate(Date statisticsTime) {
        this.statisticsDate = statisticsTime;
    }

    public ClassHourStatistics(String id, String coachId, Date createTime, Date statisticsDate, Integer classNumber, String organizationId) {
        this.id = id;
        this.coachId = coachId;
        this.createTime = createTime;
        this.statisticsDate = statisticsDate;
        this.classNumber = classNumber;
        this.organizationId = organizationId;
    }

    public ClassHourStatistics() {
    }
}

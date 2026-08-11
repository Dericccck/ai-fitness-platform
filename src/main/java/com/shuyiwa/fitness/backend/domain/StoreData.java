package com.shuyiwa.fitness.backend.domain;

import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.util.Date;

/**
 * 店铺数据表
 */
@Entity
@Table(indexes = {@Index(columnList = "type,statisticsDate", unique = true)})
public class StoreData {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Column
    private String type;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Column
    @Temporal(TemporalType.DATE)
    private Date statisticsDate;

    @Column
    private Integer data;

    public StoreData() {
    }

    public StoreData(String id, String type, Date createTime, Date statisticsDate, Integer data) {
        this.id = id;
        this.type = type;
        this.createTime = createTime;
        this.statisticsDate = statisticsDate;
        this.data = data;
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Date getStatisticsDate() {
        return statisticsDate;
    }

    public void setStatisticsDate(Date date) {
        this.statisticsDate = date;
    }

    public Integer getData() {
        return data;
    }

    public void setData(Integer data) {
        this.data = data;
    }
}

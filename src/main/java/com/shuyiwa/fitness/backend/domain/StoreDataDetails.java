package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import org.hibernate.annotations.GenericGenerator;
import org.hibernate.annotations.Type;

import javax.persistence.*;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;

/**
 * 店铺数据详情表
 */
@Entity
public class StoreDataDetails {

    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Column
    private String behavior;

    // 1.新客  2.修改合约  3.退款  4.其他
    @Column
    private Integer type;

    @Column
    private String dataId;

    @Column
    private Integer execNum = 0;

    @Column
    private Integer execAmount = 0;

    @Column
    private Integer revenueAmount = 0;

    @Column
    @Type(type = "text")
    private String coachIds;

    @Transient
    private Map<String, Object> properties = new HashMap<>();

    public StoreDataDetails() {
    }

    public StoreDataDetails(String id, Date createTime, String behavior, Integer type, String dataId, Integer execNum, Integer execAmount, Integer revenueAmount) {
        this.id = id;
        this.createTime = createTime;
        this.behavior = behavior;
        this.type = type;
        this.dataId = dataId;
        this.execNum = execNum;
        this.execAmount = execAmount;
        this.revenueAmount = revenueAmount;
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

    public String getBehavior() {
        return behavior;
    }

    public void setBehavior(String behavior) {
        this.behavior = behavior;
    }

    public Integer getType() {
        return type;
    }

    public String getCoachIds() {
        return coachIds;
    }

    public void setCoachIds(String coachIds) {
        this.coachIds = coachIds;
    }

    public void setType(Integer type) {
        this.type = type;
    }

    public String getDataId() {
        return dataId;
    }

    public void setDataId(String dataId) {
        this.dataId = dataId;
    }

    public Integer getExecNum() {
        return execNum;
    }

    public void setExecNum(Integer execNum) {
        this.execNum = execNum;
    }

    public Integer getExecAmount() {
        return execAmount;
    }

    public void setExecAmount(Integer execAmount) {
        this.execAmount = execAmount;
    }

    public Integer getRevenueAmount() {
        return revenueAmount;
    }

    public void setRevenueAmount(Integer revenueAmount) {
        this.revenueAmount = revenueAmount;
    }
    @JsonAnyGetter
    public Map<String, Object> getProperties() {
        return properties;
    }

    @JsonAnySetter
    public void setProperty(String name, Object value) {
        properties.put(name, value);
    }
}

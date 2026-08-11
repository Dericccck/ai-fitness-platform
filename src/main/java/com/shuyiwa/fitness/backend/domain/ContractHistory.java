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

/**
 * 合约历史表
 */
@Entity
public class ContractHistory implements Serializable {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    /**
     * 合约id
     */
    @Column(length = 32)
    private String contractId;

    /**
     * 修改时间
     */
    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    /**
     * 修改之前的数据
     */
    @Column
    @Type(type = "text")
    private String beforeData;

    /**
     * 修改的数据
     */
    @Column
    @Type(type = "text")
    private String updateData;

    @Column(length = 32)
    private String organizationId;

    @ManyToOne
    @JoinColumn(name = "update_login_user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser updateLoginUser;


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

    public ContractHistory() {
    }

    public ContractHistory(String id, String contractId, Date createTime,  String beforeData, String updateData, String organizationId) {
        this.id = id;
        this.contractId = contractId;
        this.createTime = createTime;
        this.beforeData = beforeData;
        this.updateData = updateData;
        this.organizationId = organizationId;
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public LoginUser getUpdateLoginUser() {
        return updateLoginUser;
    }

    public void setUpdateLoginUser(LoginUser updateLoginUser) {
        this.updateLoginUser = updateLoginUser;
    }

    public String getContractId() {
        return contractId;
    }

    public void setContractId(String contractId) {
        this.contractId = contractId;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public String getBeforeData() {
        return beforeData;
    }

    public void setBeforeData(String beforeData) {
        this.beforeData = beforeData;
    }

    public String getUpdateData() {
        return updateData;
    }

    public void setUpdateData(String updateData) {
        this.updateData = updateData;
    }

    public String getOrganizationId() {
        return organizationId;
    }

    public void setOrganizationId(String organizationId) {
        this.organizationId = organizationId;
    }
}

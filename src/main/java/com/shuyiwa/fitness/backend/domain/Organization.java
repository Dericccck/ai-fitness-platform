package com.shuyiwa.fitness.backend.domain;


import com.fasterxml.jackson.annotation.*;
import org.apache.commons.lang3.StringUtils;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import javax.validation.constraints.Digits;
import java.math.BigDecimal;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

import static com.shuyiwa.fitness.backend.Utils.injectSpace;

/**
 * 机构
 */
@JsonIdentityInfo(generator = ObjectIdGenerators.PropertyGenerator.class, property = "id", resolver = EntityIdResolver.class, scope = Organization.class)
@Entity
@Table(indexes = @Index(columnList = "nextUpdateHeatTime"))
public class Organization {
    public static final int SEARCH_LEN = 1000;
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;


    @Column
    private String name;

    /*用于全文检索*/
    @JsonIgnore
    @Column(length = SEARCH_LEN)
    private String search;

    @Column
    private String address;

    @Column(length = 1024)
    private String summary;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date publishTime;

    @Column
    private boolean virtualOrganization = false;

    //审核状态.
    @Column(length = 16)
    private Integer auditStatus = 1;

    private int priority = 0;

    @Column
    private String logo;
    @Column
    private String logoPath;
    @Column
    private String logoDiskUrl;

    @Column
    @Temporal(TemporalType.TIMESTAMP)
    private Date nextSummaryOrgVirtualTime;

    @Column
    @Temporal(TemporalType.TIMESTAMP)
    private Date lastSummaryOrgVirtualTime;


    @Column
    @Digits(integer = 11, fraction = 4)
    private BigDecimal heat;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date nextUpdateHeatTime;

    /**
     * 创建者
     */
    @ManyToOne
    @JoinColumn(name = "create_login_user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser createLoginUser;


    @Enumerated(EnumType.STRING)
    @Column(length = 20)
    private OrganizationType organizationType;

    @Transient
    private String organizationTypeLabel;


    @Lob
    @Column
    private String detailBody;
    @Lob
    @Column
    private String detailBodyRaw;

    @Version
    private int version = 0;


    @Transient
    private Map<String, Object> properties = new HashMap<>();

    public String getOrganizationTypeLabel() {
        return Optional.ofNullable(organizationType).map(OrganizationType::getLabel).orElse(null);
    }

    public void setOrganizationTypeLabel(String organizationTypeLabel) {
        this.organizationTypeLabel = organizationTypeLabel;
    }

    public OrganizationType getOrganizationType() {
        return organizationType;
    }

    public void setOrganizationType(OrganizationType organizationType) {
        this.organizationType = organizationType;
    }

    public String getLogoPath() {
        return logoPath;
    }

    public void setLogoPath(String logoPath) {
        this.logoPath = logoPath;
    }

    public String getLogoDiskUrl() {
        return logoDiskUrl;
    }

    public void setLogoDiskUrl(String logoDiskUrl) {
        this.logoDiskUrl = logoDiskUrl;
    }

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

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getSummary() {
        return summary;
    }

    public void setSummary(String summary) {
        this.summary = summary;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Date getPublishTime() {
        return publishTime;
    }

    public void setPublishTime(Date publishTime) {
        this.publishTime = publishTime;
    }

    public LoginUser getCreateLoginUser() {
        return createLoginUser;
    }

    public void setCreateLoginUser(LoginUser createLoginUser) {
        this.createLoginUser = createLoginUser;
    }

    public String getLogo() {
        return logo;
    }

    public void setLogo(String logo) {
        this.logo = logo;
    }

    public boolean isVirtualOrganization() {
        return virtualOrganization;
    }

    public void setVirtualOrganization(boolean virtualOrganization) {
        this.virtualOrganization = virtualOrganization;
    }

    public String getAddress() {
        return address;
    }

    public void setAddress(String address) {
        this.address = address;
    }

    public int getPriority() {
        return priority;
    }

    public void setPriority(int priority) {
        this.priority = priority;
    }

    public String getSearch() {
        return search;
    }

    public void setSearch(String search) {
        this.search = StringUtils.substring(search, 0, SEARCH_LEN - 1);
    }

    public void resetSearch() {
        setSearch(injectSpace(name + "," + getOrganizationTypeLabel()));
    }

    public enum OrganizationType {
        official("官方"), community("公共"), society("社会"), business("商务");
        private final String label;

        OrganizationType(String label) {
            this.label = label;
        }

        public String getLabel() {
            return label;
        }


    }

    public BigDecimal getHeat() {
        return heat;
    }

    public void setHeat(BigDecimal heat) {
        this.heat = heat;
    }

    public Date getNextUpdateHeatTime() {
        return nextUpdateHeatTime;
    }

    public void setNextUpdateHeatTime(Date nextUpdateHeatTime) {
        this.nextUpdateHeatTime = nextUpdateHeatTime;
    }

    public String getDetailBody() {
        return detailBody;
    }

    public void setDetailBody(String detailBody) {
        this.detailBody = detailBody;
    }

    public String getDetailBodyRaw() {
        return detailBodyRaw;
    }

    public void setDetailBodyRaw(String detailBodyRaw) {
        this.detailBodyRaw = detailBodyRaw;
    }

    public Date getNextSummaryOrgVirtualTime() {
        return nextSummaryOrgVirtualTime;
    }

    public void setNextSummaryOrgVirtualTime(Date nextSummaryOrgVirtualTime) {
        this.nextSummaryOrgVirtualTime = nextSummaryOrgVirtualTime;
    }

    public int getVersion() {
        return version;
    }

    public void setVersion(int version) {
        this.version = version;
    }

    public Date getLastSummaryOrgVirtualTime() {
        return lastSummaryOrgVirtualTime;
    }

    public void setLastSummaryOrgVirtualTime(Date lastSummaryOrgVirtualTime) {
        this.lastSummaryOrgVirtualTime = lastSummaryOrgVirtualTime;
    }

    public Integer getAuditStatus() {
        return auditStatus;
    }

    public void setAuditStatus(Integer auditStatus) {
        this.auditStatus = auditStatus;
    }
}


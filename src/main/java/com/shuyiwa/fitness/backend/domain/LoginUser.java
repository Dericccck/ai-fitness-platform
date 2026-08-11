package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.*;
import com.shuyiwa.fitness.backend.domain.dict.Sex;
import org.hibernate.annotations.DynamicUpdate;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.io.Serializable;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;

@JsonIdentityInfo(generator = ObjectIdGenerators.PropertyGenerator.class, property = "id", resolver = EntityIdResolver.class, scope = LoginUser.class)
@Entity
@Table(uniqueConstraints = {
//        @UniqueConstraint(columnNames = "name"),
        @UniqueConstraint(columnNames = "phone")
},
        indexes = {
                @Index(columnList = "isManager"),
                @Index(columnList = "lastVotesAssignTime"),
                @Index(columnList = "weiXinUnionId,weiXinUnionIdIndex", unique = true)
        })
@DynamicUpdate
public class LoginUser implements Serializable {

    private static final long serialVersionUID = 42L;

    public static final int APP_SEARCH_LEN = 2000;


    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;


    /*用于全文检索*/
    @JsonIgnore
    @Column(length = APP_SEARCH_LEN)
    private String appSearch;


    @Column(length = 36)
    @EditTimes
    private String name;

    @Column(length = 13, nullable = false)
    private String phone;

    @Column(length = 1204)
    private String avatar;

    @Column(length = 1024)
    private String avatarPath;

    @Column(length = 1024)
    private String avatarDiskUrl;

    @Column
    @Enumerated(EnumType.STRING)
    @EditTimes
    private Sex sex;

    @JsonIgnore
    @Column(length = 128)
    private String password;

    @Column(length = 1, nullable = false)
    private Boolean enabled = true;

    @Column
    private Boolean acceptNotify = true;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Temporal(TemporalType.TIMESTAMP)
    @Column
    private Date lastLoginTime;

    @Column(length = 1024)
    private String lastLoginUa;

    @Column
    private String lastLoginVersion;

    @Column
    private String lastLoginIp;

    @Column(length = 2048)
    private String introduction;


    @Temporal(TemporalType.TIMESTAMP)
    @Column
    private Date lastFetchSystemMessageTime;


    @Transient
    private boolean editable;

    @Column
    private Long editTimes = 0L;

    @Column
    private Long maxEditTimes = 100L;

    //2020-05-16以后弃用
    //2020-07-16以后可删除
    @Deprecated
    @Column
    private Long availableVotes = 2L;

    //2020-05-16以后弃用
    //2020-07-16以后可删除
    @Deprecated
    @Column(nullable = false)
    private long dailyVotes = 0L;

    @Column
    //2020-05-16以后弃用
    //2020-07-16以后可删除
    @Deprecated
    private Long dailyScopedVotes = 0L;
    @Column
    private Boolean isManager = false;
    /**
     * 为true时，该用户看到的时已开发以及开发中的内容，为false时，只能看当前已经开发完成的内容。用于开发人员测试新功能。
     */
    @Column
    private boolean beta = false;
    /**
     * 上次分得新票的时间
     */
    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date lastVotesAssignTime;
    @Transient
    private Map<String, Object> properties = new HashMap<>();
    @Version
    private Long version = 0l;
    @Column
    private String weiXinOpenId;
    @Column
    private String weiXinUnionId;

    /**
     * 因为已经有几个用户绑定了同一个微信了，所以加上这个字段才能建立唯一索引
     */
    @Column
    private int weiXinUnionIdIndex = 0;

    @Column(length = 2048)
    private String weiXinInfo;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date weiXinBindTime;

    @Column(length = 18)
    private String idCard;

    @Temporal(TemporalType.TIMESTAMP)
    @Column
    private Date birthDay;

    public String getIdCard() {
        return idCard;
    }

    public void setIdCard(String idCard) {
        this.idCard = idCard;
    }

    public Date getBirthDay() {
        return birthDay;
    }

    public void setBirthDay(Date birthDay) {
        this.birthDay = birthDay;
    }

    public Long getDailyScopedVotes() {
        return dailyScopedVotes;
    }

    public void setDailyScopedVotes(Long dailyScopedVotes) {
        this.dailyScopedVotes = dailyScopedVotes;
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

    public String getPhone() {
        return phone;
    }

    public void setPhone(String phone) {
        this.phone = phone;
    }

    public String getAvatar() {
        return avatar;
    }

    public void setAvatar(String avatar) {
        this.avatar = avatar;
    }

    public String getAvatarPath() {
        return avatarPath;
    }

    public void setAvatarPath(String avatarPath) {
        this.avatarPath = avatarPath;
    }

    public String getAvatarDiskUrl() {
        return avatarDiskUrl;
    }

    public void setAvatarDiskUrl(String avatarDiskUrl) {
        this.avatarDiskUrl = avatarDiskUrl;
    }

    public Sex getSex() {
        return sex;
    }

    public void setSex(Sex sex) {
        this.sex = sex;
    }

    public String getPassword() {
        return password;
    }

    public void setPassword(String password) {
        this.password = password;
    }

    public Boolean getEnabled() {
        return enabled;
    }

    public void setEnabled(Boolean enabled) {
        this.enabled = enabled;
    }

    public Boolean getAcceptNotify() {
        return acceptNotify;
    }

    public void setAcceptNotify(Boolean acceptNotify) {
        this.acceptNotify = acceptNotify;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Date getLastFetchSystemMessageTime() {
        return lastFetchSystemMessageTime;
    }

    public void setLastFetchSystemMessageTime(Date lastFetchSystemMessageTime) {
        this.lastFetchSystemMessageTime = lastFetchSystemMessageTime;
    }

    public boolean isEditable() {
        return editTimes < maxEditTimes;
    }

    public void setEditable(boolean editable) {
        this.editable = editable;
    }

    public Long getEditTimes() {
        return editTimes;
    }

    public void setEditTimes(Long editTimes) {
        this.editTimes = editTimes;
    }

    public Long getAvailableVotes() {
        return 100l;
//        return availableVotes;
    }

    public void setAvailableVotes(Long availableVotes) {
        this.availableVotes = availableVotes;
    }

    public Long getMaxEditTimes() {
        return maxEditTimes;
    }

    public void setMaxEditTimes(Long maxEditTimes) {
        this.maxEditTimes = maxEditTimes;
    }

    public Date getLastVotesAssignTime() {
        return lastVotesAssignTime;
    }

    public void setLastVotesAssignTime(Date lastVotesAssignTime) {
        this.lastVotesAssignTime = lastVotesAssignTime;
    }

    public Boolean getManager() {
        return isManager;
    }

    public void setManager(Boolean manager) {
        isManager = manager;
    }

    public boolean isBeta() {
        return beta;
    }

    public void setBeta(boolean beta) {
        this.beta = beta;
    }

    public Date getLastLoginTime() {
        return lastLoginTime;
    }

    public void setLastLoginTime(Date lastLoginTime) {
        this.lastLoginTime = lastLoginTime;
    }

    public String getLastLoginUa() {
        return lastLoginUa;
    }

    public void setLastLoginUa(String lastLoginUa) {
        this.lastLoginUa = lastLoginUa;
    }

    public String getLastLoginVersion() {
        return lastLoginVersion;
    }

    public void setLastLoginVersion(String lastLoginVersion) {
        this.lastLoginVersion = lastLoginVersion;
    }

    public String getLastLoginIp() {
        return lastLoginIp;
    }

    public void setLastLoginIp(String lastLoginIp) {
        this.lastLoginIp = lastLoginIp;
    }

    public long getDailyVotes() {
        return dailyVotes;
    }

    public void setDailyVotes(long dailyVotes) {
        this.dailyVotes = dailyVotes;
    }

    public Long getVersion() {
        return version;
    }

    public void setVersion(Long version) {
        this.version = version;
    }

    public String getWeiXinOpenId() {
        return weiXinOpenId;
    }

    public void setWeiXinOpenId(String weiXinOpenId) {
        this.weiXinOpenId = weiXinOpenId;
    }

    public String getWeiXinUnionId() {
        return weiXinUnionId;
    }

    public void setWeiXinUnionId(String weiXinUnionId) {
        this.weiXinUnionId = weiXinUnionId;
    }

    public String getWeiXinInfo() {
        return weiXinInfo;
    }

    public void setWeiXinInfo(String weiXinInfo) {
        this.weiXinInfo = weiXinInfo;
    }

    public Date getWeiXinBindTime() {
        return weiXinBindTime;
    }

    public void setWeiXinBindTime(Date weiXinBindTime) {
        this.weiXinBindTime = weiXinBindTime;
    }

    public String getIntroduction() {
        return introduction;
    }

    public void setIntroduction(String introduction) {
        this.introduction = introduction;
    }

    public int getWeiXinUnionIdIndex() {
        return weiXinUnionIdIndex;
    }

    public void setWeiXinUnionIdIndex(int weiXinUnionIdIndex) {
        this.weiXinUnionIdIndex = weiXinUnionIdIndex;
    }

    @Target(ElementType.FIELD)
    @Retention(RetentionPolicy.RUNTIME)
    public @interface EditTimes {
    }
}

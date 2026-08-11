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
        @Index(columnList = "receive_login_user_id,organization_id"),
})
public class News {
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
     * 接收者
     */
    @ManyToOne
    @JoinColumn(name = "receive_login_user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser receiveLoginUser;

    //1确认，2拒绝，0待确认
    @Column(length = 16)
    private int handle_result = 0;

    @Column
    @Temporal(TemporalType.TIMESTAMP)
    private Date handleTime;

    @Column
    @Enumerated(EnumType.STRING)
    private NewsType newsType;

    @Column(length = 4096)
    private String content;

    @Column(length = 4096)
    private String newsBody;

    @Column(length = 32)
    private String entityId;

    @Column
    private boolean deleted = false;

    @ManyToOne
    @JoinColumn(name = "organization_id")
    @JsonIdentityReference(alwaysAsId = true)
    private Organization organization;

    @Column(length = 32)
    private String handleUserId;

    @Version
    private int version=0;


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


    public String getHandleUserId() {
        return handleUserId;
    }

    public void setHandleUserId(String handleUserId) {
        this.handleUserId = handleUserId;
    }

    public int getVersion() {
        return version;
    }

    public void setVersion(int version) {
        this.version = version;
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

    public LoginUser getReceiveLoginUser() {
        return receiveLoginUser;
    }

    public void setReceiveLoginUser(LoginUser receiveLoginUser) {
        this.receiveLoginUser = receiveLoginUser;
    }

    public int getHandle_result() {
        return handle_result;
    }

    public void setHandle_result(int handle_result) {
        this.handle_result = handle_result;
    }

    public Date getHandleTime() {
        return handleTime;
    }

    public void setHandleTime(Date handleTime) {
        this.handleTime = handleTime;
    }

    public NewsType getNewsType() {
        return newsType;
    }

    public void setNewsType(NewsType newsType) {
        this.newsType = newsType;
    }

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }

    public String getNewsBody() {
        return newsBody;
    }

    public void setNewsBody(String newsBody) {
        this.newsBody = newsBody;
    }

    public String getEntityId() {
        return entityId;
    }

    public void setEntityId(String entityId) {
        this.entityId = entityId;
    }

    public boolean isDeleted() {
        return deleted;
    }

    public void setDeleted(boolean deleted) {
        this.deleted = deleted;
    }

    public Organization getOrganization() {
        return organization;
    }

    public void setOrganization(Organization organization) {
        this.organization = organization;
    }

    public News() {
    }

    public News(LoginUser createLoginUser, LoginUser receiveLoginUser, NewsType newsType, String content, String entityId, Organization organization,String newsBody) {
        this.createLoginUser = createLoginUser;
        this.receiveLoginUser = receiveLoginUser;
        this.newsType = newsType;
        this.content = content;
        this.entityId = entityId;
        this.organization = organization;
        this.newsBody = newsBody;
    }
}

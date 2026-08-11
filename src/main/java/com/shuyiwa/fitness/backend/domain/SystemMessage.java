package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonIdentityReference;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;

/**
 * 系统消息，contest为空时，对所有人可见，contest不为空时，只对参与指定contest的人可见
 */
@Entity
public class SystemMessage {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Column(length = 512)
    private String content;


    @ManyToOne
    @JsonIdentityReference(alwaysAsId = true)
    private MessageTask messageTask;


    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date updateTime;

    @Column(length = 20)
    @Enumerated(EnumType.STRING)
    private FeedItem.EntityType linkEntityType;

    @Column(length = 500)
    private String linkEntity;

    @Column
    private String linkText;

    @Column
    private String tag;

    @Transient
    private Map<String, Object> properties = new HashMap<>();

    @ManyToOne
    @JsonIdentityReference(alwaysAsId = true)
    @JoinColumn(name = "source_login_user_id", nullable = false)
    private LoginUser sourceLoginUser;

    public LoginUser getSourceLoginUser() {
        return sourceLoginUser;
    }

    public void setSourceLoginUser(LoginUser sourceLoginUser) {
        this.sourceLoginUser = sourceLoginUser;
    }

    public String getTag() {
        return tag;
    }

    public void setTag(String tag) {
        this.tag = tag;
    }


    @JsonAnyGetter
    public Map<String, Object> getProperties() {
        return properties;
    }

    public void setProperties(Map<String, Object> properties) {
        this.properties = properties;
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

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Date getUpdateTime() {
        return updateTime;
    }

    public void setUpdateTime(Date updateTime) {
        this.updateTime = updateTime;
    }

    public MessageTask getMessageTask() {
        return messageTask;
    }

    public void setMessageTask(MessageTask messageTask) {
        this.messageTask = messageTask;
    }

    public FeedItem.EntityType getLinkEntityType() {
        return linkEntityType;
    }

    public void setLinkEntityType(FeedItem.EntityType linkEntityType) {
        this.linkEntityType = linkEntityType;
    }

    public String getLinkEntity() {
        return linkEntity;
    }

    public void setLinkEntity(String linkEntity) {
        this.linkEntity = linkEntity;
    }

    public String getLinkText() {
        return linkText;
    }

    public void setLinkText(String linkText) {
        this.linkText = linkText;
    }
}

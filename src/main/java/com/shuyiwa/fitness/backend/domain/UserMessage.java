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
 * 用户消息，包括发给用户个人的消息和领取的系统消息。
 * 获取用户消息时，先根据记录的用户最后一次领取系统消息的时间，领取新消息并更新最后一次领取消息的时间，然后再返回本表的记录
 */
@Entity
@Table(indexes = {@Index(columnList = "login_user_id,messageType,createTime"), @Index(columnList = "systemMessageId")})
public class UserMessage {

    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;


    @ManyToOne
    @JsonIdentityReference(alwaysAsId = true)
    private MessageTask messageTask;

    @Column(length = 512)
    private String content;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = true, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date updateTime;

    @Column(nullable = false, length = 32)
    @Enumerated(EnumType.STRING)
    private MessageType messageType;

    @Column(length = 32)
    private String systemMessageId;

    @Column(length = 20)
    @Enumerated(EnumType.STRING)
    private FeedItem.EntityType linkEntityType;

    @Column(length = 500)
    private String linkEntity;

    @Column
    private String linkText;

    @ManyToOne
    @JsonIdentityReference(alwaysAsId = true)
    @JoinColumn(name = "source_login_user_id")
    private LoginUser sourceLoginUser;

    @ManyToOne
    @JoinColumn(name = "login_user_id", nullable = false)
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser loginUser;

    @Column(nullable = false)
    @Enumerated(EnumType.STRING)
    private UserMessageStatus status = UserMessageStatus.UNREAD;
    @Column
    private String tag;
    @Transient
    private Map<String, Object> properties = new HashMap<>();

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

    public LoginUser getSourceLoginUser() {
        return sourceLoginUser;
    }

    public void setSourceLoginUser(LoginUser sourceLoginUser) {
        this.sourceLoginUser = sourceLoginUser;
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

    public LoginUser getLoginUser() {
        return loginUser;
    }

    public void setLoginUser(LoginUser loginUser) {
        this.loginUser = loginUser;
    }

    public UserMessageStatus getStatus() {
        return status;
    }

    public void setStatus(UserMessageStatus status) {
        this.status = status;
    }

    public MessageType getMessageType() {
        return messageType;
    }

    public void setMessageType(MessageType messageType) {
        this.messageType = messageType;
    }

    public String getSystemMessageId() {
        return systemMessageId;
    }

    public void setSystemMessageId(String systemMessageId) {
        this.systemMessageId = systemMessageId;
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

    public enum UserMessageStatus {
        UNREAD, READ, DELETED
    }

    public enum MessageType {
        SYSTEM, USER
    }
}

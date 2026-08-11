package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonIdentityReference;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;

/**
 * 消息任务
 */
@Entity
public class MessageTask {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Column
    private String content;


    @Column(length = 20)
    @Enumerated(EnumType.STRING)
    private FeedItem.EntityType linkEntityType;

    @Column(length = 500)
    private String linkEntity;

    @Column
    private String linkText;


    @Column
    private String receiver;

    @Lob
    @Column
    private String phoneList;

    @Column
    private boolean copyToApp;

    @Column(nullable = false)
    private String channel;

    @Temporal(TemporalType.TIMESTAMP)
    private Date publishTime;

    @Temporal(TemporalType.DATE)
    private Date startPublishTime;
    @Column
    private boolean deleted = false;
    @Column(nullable = false)
    @Enumerated(EnumType.STRING)
    private TaskStatus status = TaskStatus.INIT;
    @Column
    @Enumerated(EnumType.STRING)
    @JsonFormat(shape = JsonFormat.Shape.STRING)
    private Sms.Template smsTemplate;
    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;
    @ManyToOne
    @JsonIdentityReference(alwaysAsId = true)
    @JoinColumn(name = "source_login_user_id", nullable = false)
    private LoginUser sourceLoginUser;
    @Transient
    private Map<String, Object> properties = new HashMap<>();

    public Date getStartPublishTime() {
        return startPublishTime;
    }

    public void setStartPublishTime(Date startPublishTime) {
        this.startPublishTime = startPublishTime;
    }

    public boolean isDeleted() {
        return deleted;
    }

    public void setDeleted(boolean deleted) {
        this.deleted = deleted;
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

    public Sms.Template getSmsTemplate() {
        return smsTemplate;
    }

    public void setSmsTemplate(Sms.Template smsTemplate) {
        this.smsTemplate = smsTemplate;
    }

    public String getReceiver() {
        return receiver;
    }

    public void setReceiver(String receiver) {
        this.receiver = receiver;
    }

    public boolean isCopyToApp() {
        return copyToApp;
    }

    public void setCopyToApp(boolean copyToApp) {
        this.copyToApp = copyToApp;
    }

    public String getChannel() {
        return channel;
    }

    public void setChannel(String channel) {
        this.channel = channel;
    }

    public Date getPublishTime() {
        return publishTime;
    }

    public void setPublishTime(Date publishTime) {
        this.publishTime = publishTime;
    }

    public TaskStatus getStatus() {
        return status;
    }

    public void setStatus(TaskStatus status) {
        this.status = status;
    }

    public LoginUser getSourceLoginUser() {
        return sourceLoginUser;
    }

    public void setSourceLoginUser(LoginUser sourceLoginUser) {
        this.sourceLoginUser = sourceLoginUser;
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

    public enum Channel {
        APP("通知"), SMS("短信");

        private final String desc;

        Channel(String desc) {
            this.desc = desc;
        }

        public String getDesc() {
            return desc;
        }
    }

    public enum Receiver {
        ALL("全体"), APPLY("报名")
//        , TEST("测试")
        ;

        private final String desc;

        Receiver(String desc) {
            this.desc = desc;
        }

        public String getDesc() {
            return desc;
        }
    }

    public enum TaskStatus {
        INIT("等待发布"), PUBLISHED("已发布");
        private final String desc;

        TaskStatus(String desc) {
            this.desc = desc;
        }

        public String getDesc() {
            return desc;
        }
    }

    public String getPhoneList() {
        return phoneList;
    }

    public void setPhoneList(String phoneList) {
        this.phoneList = phoneList;
    }
}

package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonIdentityReference;
import org.hibernate.annotations.GenericGenerator;

import javax.persistence.*;
import java.util.Date;

@Entity
public class NewsWechat {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Column
    @Temporal(TemporalType.TIMESTAMP)
    private Date sendTime;

    @Column(length = 4096)
    private String content;

    @Column(length = 32)
    private String loginUserId;

    @Column(length = 32)
    private String userName;


    //0待发送，1已发送，2发送失败
    @Column(length = 16)
    private int status = 0;

    @Column
    private String weiXinOpenId;

    @Column
    private String sendResult;

    public String getLoginUserId() {
        return loginUserId;
    }

    public void setLoginUserId(String loginUserId) {
        this.loginUserId = loginUserId;
    }

    public String getUserName() {
        return userName;
    }

    public void setUserName(String userName) {
        this.userName = userName;
    }

    /**
     * 创建者
     */
    @ManyToOne
    @JoinColumn(name = "create_login_user_id")
    @JsonIdentityReference(alwaysAsId = true)
    private LoginUser createLoginUser;

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

    public Date getSendTime() {
        return sendTime;
    }

    public void setSendTime(Date sendTime) {
        this.sendTime = sendTime;
    }

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }

    public int getStatus() {
        return status;
    }

    public void setStatus(int status) {
        this.status = status;
    }

    public String getWeiXinOpenId() {
        return weiXinOpenId;
    }

    public void setWeiXinOpenId(String weiXinOpenId) {
        this.weiXinOpenId = weiXinOpenId;
    }

    public LoginUser getCreateLoginUser() {
        return createLoginUser;
    }

    public void setCreateLoginUser(LoginUser createLoginUser) {
        this.createLoginUser = createLoginUser;
    }

    public String getSendResult() {
        return sendResult;
    }

    public void setSendResult(String sendResult) {
        this.sendResult = sendResult;
    }
}

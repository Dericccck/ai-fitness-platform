package com.shuyiwa.fitness.backend.event;

import com.shuyiwa.fitness.backend.domain.LoginUser;

import java.util.Map;

public class NewsWechatEvent {
    private String wxOpenId;//接收人openid
    private LoginUser loginUser; //创建人/留言人
    private String sender;//留言人名称
    private String content;//发送内容


    public String getWxOpenId() {
        return wxOpenId;
    }

    public void setWxOpenId(String wxOpenId) {
        this.wxOpenId = wxOpenId;
    }

    public LoginUser getLoginUser() {
        return loginUser;
    }

    public void setLoginUser(LoginUser loginUser) {
        this.loginUser = loginUser;
    }

    public String getSender() {
        return sender;
    }

    public void setSender(String sender) {
        this.sender = sender;
    }

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }
}

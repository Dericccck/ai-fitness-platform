package com.shuyiwa.fitness.backend.event;

import com.shuyiwa.fitness.backend.domain.LoginUser;

public class LoginUserCreatedEvent {
    private LoginUser loginUser;

    public LoginUser getLoginUser() {
        return loginUser;
    }

    public void setLoginUser(LoginUser loginUser) {
        this.loginUser = loginUser;
    }
}

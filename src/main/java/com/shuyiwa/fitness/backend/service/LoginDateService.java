package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.UserLoginDateRepository;
import com.shuyiwa.fitness.backend.event.LoginUserActivityEvent;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class LoginDateService {
    @Autowired
    UserLoginDateRepository userLoginDateRepository;

    @Transactional(rollbackFor = Throwable.class)
    @EventListener
    public void onLoginSuccessEvent(LoginUserActivityEvent event) {
        String loginUserId = event.getLoginUserId();
        if (loginUserId != null) {
            userLoginDateRepository.insertIgnore(loginUserId);

        }
    }
}

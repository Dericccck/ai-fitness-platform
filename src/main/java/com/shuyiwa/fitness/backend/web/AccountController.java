package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.Account;
import com.shuyiwa.fitness.backend.domain.CurrencyType;
import com.shuyiwa.fitness.backend.domain.LoginUserRepository;
import com.shuyiwa.fitness.backend.service.AccountService;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@RestController
public class AccountController {
    @Autowired
    AccountService accountService;
    @Autowired
    LoginUserRepository loginUserRepository;


    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "获取我的账户")
    @RequestMapping(value = "api/account/my", method = RequestMethod.GET)
    Account my(@AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        return accountService.findOrCreateAccount(Optional.ofNullable(frogUserDetails).map(f -> f.getLoginUser(loginUserRepository)), CurrencyType.point);
    }

    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN')")
    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "系统奖励积分")
    @RequestMapping(value = "api/account/reward", method = RequestMethod.POST)
    @Transactional
    List<String> reward(
            @RequestParam("value") BigDecimal value,
            @RequestParam("phone") String phone,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        List<String> list = new ArrayList<>();
        for (String p : phone.split(",")) {
            BigDecimal oldBalance = accountService.findOrCreateAccount(loginUserRepository.findByPhone(p), CurrencyType.point).getBalance();
            Account account = accountService.reward("系统赠送", loginUserRepository.findByPhone(p), value);
            list.add(p + ":" + oldBalance + "->" + account.getBalance());
        }
        return list;
    }
}

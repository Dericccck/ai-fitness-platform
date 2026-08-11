package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.LoginUser;
import com.shuyiwa.fitness.backend.domain.LoginUserAuthority;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.domain.LoginUserAuthorityRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

@Service
public class AuthorityService {
    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;
    @Autowired
    LoginUserService loginUserService;

    public void replaceByPhoneList(Authority authority, String entityId, String[] phoneList) {
        Set<String> mergedSet = Arrays.stream(phoneList).collect(Collectors.toSet());
        List<LoginUserAuthority> oldList = loginUserAuthorityRepository.findByAuthorityAndEntityId(authority, entityId);
        Set<String> oldSet = oldList.stream().map(LoginUserAuthority::getLoginUser).map(LoginUser::getPhone).collect(Collectors.toSet());
        Arrays.stream(phoneList)
                .filter(phone -> !oldSet.contains(phone))
                .filter(phone -> !StringUtils.isEmpty(phone))
                .forEach(phone -> {
                    //新增
                    LoginUser loginUser = loginUserService.createLoginUser(phone,null);
                    LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
                    loginUserAuthority.setAuthority(authority);
                    loginUserAuthority.setEntityId(entityId);
                    loginUserAuthority.setLoginUser(loginUser);
                    loginUserAuthorityRepository.save(loginUserAuthority);

                });
        oldList.stream()
                .filter(a -> !mergedSet.contains(a.getLoginUser().getPhone()))
                .forEach(a -> {
                    //删除
                    loginUserAuthorityRepository.delete(a);
                });

    }
}

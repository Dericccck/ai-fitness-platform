package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.DescContent;
import com.shuyiwa.fitness.backend.domain.LoginUser;
import com.shuyiwa.fitness.backend.domain.LoginUserRepository;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.service.DescContentService;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DescContentController {

    @Autowired
    DescContentService descContentService;

    @Autowired
    private LoginUserRepository loginUserRepository;


    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "保存反馈信息")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/descContent/save", method = RequestMethod.POST)
    DescContent save(@RequestBody DescContent descContent,
                     @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        descContent.setLoginUser(loginUser);
        return descContentService.save(descContent);
    }





}

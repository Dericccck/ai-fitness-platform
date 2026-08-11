package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.service.ClassHourRecordService;
import com.shuyiwa.fitness.backend.service.NewsService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.*;

import java.util.Date;

@RestController
public class ClassHourRecordsController {

    @Autowired
    ClassHourRecordService classHourRecordService;

    @Autowired
    LoginUserRepository loginUserRepository;

    @Autowired
    OrganizationRepository organizationRepository;

    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN_ORGANIZATION') )")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "保存记录", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/record/save", method = RequestMethod.POST)
    void saveNews(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,@RequestParam String amount,
            @RequestParam String userId,@RequestParam String coach,@RequestParam(required = false,defaultValue = "0") int classHour,@RequestParam String organizationId
    ) throws FrogException {
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        ClassHourRecord hourRecord = new ClassHourRecord();
        if(StringUtils.isEmpty(amount) || "null".equals(amount)){
            throw new  FrogException(FrogException.INTERNAL_SERVER_ERROR,"充值金额必填");
        }else {
            hourRecord.setAmount(Integer.valueOf(amount));
        }
        hourRecord.setClassHour(classHour);
        hourRecord.setCreateLoginUser(loginUser);
        hourRecord.setCreateTime(new Date());
        hourRecord.setCoach(loginUserRepository.findById(coach).get());
        hourRecord.setLoginUser(loginUserRepository.findById(userId).get());
        hourRecord.setOrganization(organizationRepository.findById(organizationId).get());
        classHourRecordService.createRecord(hourRecord);
    }



    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN_ORGANIZATION') )")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "查询记录", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/records/page", method = RequestMethod.GET)
    Page<ClassHourRecord> findRecordByPage(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,@RequestParam(defaultValue = "0") int page,@RequestParam(defaultValue = "10") int size,
            @RequestParam String organizationId,@RequestParam(required = false) String userId,@RequestParam(required = false) String search
    ) throws FrogException {
//        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        return classHourRecordService.findRecordByPage(page,size,userId,organizationId,search);
    }


}

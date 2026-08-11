package com.shuyiwa.fitness.backend.web;

import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.domain.dict.SystemSettingEnum;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.sec.FrogUserDetailsService;
import com.shuyiwa.fitness.backend.service.SystemSettingsService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.*;

import java.util.Comparator;
import java.util.Date;
import java.util.List;
import java.util.Optional;

@RestController
public class SystemSettingsController {

    @Autowired
    LoginUserRepository loginUserRepository;

    @Autowired
    SystemSettingsRepository systemSettingsRepository;


    @Autowired
    OrganizationRepository organizationRepository;

    @Autowired
    SystemSettingsService systemSettingsService;


    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "保存系统设置", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/systemsetting/save", method = RequestMethod.POST)
    void saveNews(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,@RequestBody SystemSettings systemSettings
    ) throws FrogException {
//        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        if(null == systemSettings.getType()){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"参数type is required");
        }
        Optional<Organization> organizationOptional = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .filter(a -> a.getAuthorityEnum() == Authority.ADMIN_ORGANIZATION || a.getAuthorityEnum() == Authority.SUPER_ADMIN_ORGANIZATION)
                .map(a -> {
                    Optional<Organization> optionalOrganization = organizationRepository.findById(a.getEntityId());
                    //optionalOrganization.ifPresent(organization -> organization.setProperty("order", a.getAuthorityEnum() == Authority.SUPER_ADMIN_ORGANIZATION ? 0 : 1));
//                    optionalOrganization.ifPresent(organization -> organization.setProperty("superAdmin", a.getAuthorityEnum() == Authority.SUPER_ADMIN_ORGANIZATION));
                    return optionalOrganization;
                })
                .filter(a -> a.isPresent())
                .map(a -> a.get())
                //.sorted(Comparator.comparingInt(o -> (Integer) o.getProperties().get("order")))
                .findFirst();

        if(StringUtils.isEmpty(systemSettings.getId())){
            int count = systemSettingsRepository.countByTypeAndOrganization(systemSettings.getType(),organizationOptional.get());
            if(count > 0) throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"此项目已经存在");
        }
        systemSettings.setOrganization(organizationOptional.get());
        systemSettings.setAuthor(frogUserDetails.getLoginUserId());
        systemSettingsRepository.save(systemSettings);
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "查询系统设置相关数据", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/systemsetting/page", method = RequestMethod.GET)
    List<SystemSettings> findByStatus(
                                @AuthenticationPrincipal FrogUserDetails frogUserDetails,
                                @RequestParam SystemSettingEnum type,
                                @RequestParam String organizationId
                                ) throws FrogException {
//        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        Organization organization = organizationRepository.findById(organizationId).orElse(null);
        if(organization==null){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"机构不存在");
        }
        return systemSettingsRepository.findByTypeAndOrganization(type,organization);
    }


    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "教练休假", sinceTime = "2022-03-21")
    @RequestMapping(value = "api/systemsetting/holiday", method = RequestMethod.POST)
    void holiday(
            @RequestBody VacationRecord vacationRecord,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) {
        Boolean isAdminAndCoach = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .filter(authority -> (authority == Authority.COACH || authority == Authority.ADMIN_ORGANIZATION) )
                .count() > 0;
        if(isAdminAndCoach){
            //只有管理员和教练可以请假
            if (StringUtils.isEmpty(vacationRecord.getStartDate())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "请选择开始日期");
            }
            if (StringUtils.isEmpty(vacationRecord.getEndDate())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "请选择结束日期");
            }

            systemSettingsService.holiday(vacationRecord,frogUserDetails.getLoginUser(loginUserRepository));
        }
    }


    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "教练取消休假", sinceTime = "2022-03-21")
    @RequestMapping(value = "api/systemsetting/cancelHoliday", method = RequestMethod.POST)
    void cancelHoliday(
            @RequestBody JSONObject jsonObject,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) {
        String organizationId = jsonObject.getString("organizationId");
        String holidayId = jsonObject.getString("holidayId");
        Boolean isAdminAndCoach = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .filter(authority -> (authority == Authority.COACH || authority == Authority.ADMIN_ORGANIZATION) )
                .count() > 0;
        if (isAdminAndCoach){
            systemSettingsService.cancelHoliday(holidayId,organizationId);
        }
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "请假列表", sinceTime = "2022-03-21")
    @RequestMapping(value = "api/systemsetting/holidayList", method = RequestMethod.GET)
    List<VacationRecord> holidayList(
            @RequestParam(value = "organizationId") String organizationId,
            @RequestParam(value = "coachId", required = false) String coachId,
            @RequestParam(value = "days", required = false) Integer days,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ){
        Boolean isAdmin = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .filter(authority -> authority == Authority.ADMIN_ORGANIZATION)
                .count() > 0;
            List<VacationRecord> vacationRecordList = systemSettingsService.holidayList(organizationId,coachId,days,isAdmin);
            return vacationRecordList;
    }
}

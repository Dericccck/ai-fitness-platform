package com.shuyiwa.fitness.backend.web;

import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.conf.doc.DocScanner;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.AppointmentStatus;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.domain.dict.NewsType;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.sec.FrogUserDetailsService;
import com.shuyiwa.fitness.backend.service.AppointmentService;
import com.shuyiwa.fitness.backend.service.NewsService;
import com.shuyiwa.fitness.backend.service.UserAndCoachService;
import com.shuyiwa.fitness.backend.service.UserCoachHistoryService;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.*;

import javax.servlet.http.HttpServletResponse;
import java.text.ParseException;
import java.util.*;

import static com.shuyiwa.fitness.backend.conf.CacheRedisConf.METHOD;
import static com.shuyiwa.fitness.backend.conf.CacheRedisConf.S60;


@RestController
public class UserAndCoachController {

    private static final Log logger = LogFactory.getLog(UserAndCoachController.class);

    @Autowired
    UserAndCoachService userAndCoachService;
    @Autowired
    UserAndCoachRepository userAndCoachRepository;
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    NewsService newsService;
    @Autowired
    OrganizationRepository organizationRepository;
    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;
    @Autowired
    AppointmentRepository appointmentRepository;

    @Autowired
    UserCoachHistoryService userCoachHistoryService;

    @RuntimeDoc(client = {RuntimeDoc.Client.Api, RuntimeDoc.Client.Console}, desc = "教练邀约用户")
    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN_ORGANIZATION') || hasAuthority('COACH'))")
    @RequestMapping(value = "api/fitness/invite", method = RequestMethod.POST)
    void inviteUser(
            @RequestParam(value = "phone") String phone,
            @RequestParam(value = "organizationId") String organizationId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        LoginUser user = loginUserRepository.findByPhone(phone).orElseThrow(()->new FrogException(FrogException.LOGINUSER_NO_EXIST,"用户未注册"));
        //List<LoginUserAuthority> l =loginUserAuthorityRepository.findByLoginUserAndEntityId(user,organizationId);
//        Object o = l.size();
        if (userAndCoachRepository.searchCount1(organizationId, user.getId())!=0){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"已发送签约，等待用户同意");
        } else if(userAndCoachRepository.searchCount(organizationId,user.getId())!=0){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"用户已签约");
        }else if(loginUserAuthorityRepository.findByLoginUserAndEntityId(user,organizationId).size()>0){
        //主管与教练不能被邀请
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"主管与教练不能被邀请");
        }else {
           userAndCoachService.save(organizationId,user,frogUserDetails);

        }
    }


//    @Cacheable(value = S60, keyGenerator = METHOD,sync = true)
    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN_ORGANIZATION') || hasAuthority('COACH'))")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "查询用户", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/fitness/page", method = RequestMethod.GET)
    Page<UserAndCoach> findUserByPage(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "page",required = false,defaultValue = "0") int page,
            @RequestParam(value = "size",required = false,defaultValue = "10") int size,
            @RequestParam("organizationId") String organizationId,
            @RequestParam(value = "coachId",required = false)String coachId,
            @RequestParam(value = "phone",required = false)String phone,
            @RequestParam(value = "sort",required = false,defaultValue = "1") Integer sort,
            @RequestParam(value = "sortField",required = false,defaultValue = "1") Integer sortField
    ) throws FrogException {
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        List<LoginUserAuthority> list = loginUserAuthorityRepository.findByLoginUserAndEntityId(loginUser,organizationId);
        long adminCount = list.stream().filter(a->a.getAuthority() == Authority.ADMIN_ORGANIZATION).count();
        long coachCount = list.stream().filter(a->a.getAuthority() == Authority.COACH).count();
        if(adminCount>0) {
            return userAndCoachService.findAllByPage(page, size, organizationId, coachId, phone, sort, sortField);
        }

        if(adminCount==0 && coachCount>0){
            return userAndCoachService.findAllByPage(page, size, organizationId, frogUserDetails.getLoginUserId(), phone, sort, sortField);
        }
        return null;
    }

    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "更换教练")
    @RequestMapping(value = "api/fitness/change/coach", method = RequestMethod.POST)
    void changeUserAndCoach(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam("id") String id,
            @RequestParam(value = "newCoachId")String newCoachId
    ) throws FrogException {
//        if(StringUtils.isEmpty(newCoachId)){
//            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"教练不能为空");
//        }
        LoginUser admin = frogUserDetails.getLoginUser(loginUserRepository);
        UserAndCoach userAndCoach = userAndCoachRepository.findById(id).orElseThrow(()-> new FrogException(FrogException.INTERNAL_SERVER_ERROR,"数据不存在"));
        Organization organization = userAndCoach.getOrganization();
        //用户存在未销课的课程时不能换教练
        LoginUser user = userAndCoach.getUser();
//        if(appointmentRepository.findNotFinish(user.getId(),organization.getId()) > 0){
//            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"存在未销课程，无法更换教练");
//        }
        userAndCoach.setHeadCoachIds(newCoachId);
        //LoginUser newCoach = loginUserRepository.findById(newCoachId).get();
        userAndCoach.setStatus(1);
        //userAndCoach.setCoach(newCoach);
        userAndCoachRepository.save(userAndCoach);
        String[] newCoachIds = newCoachId.split(",");
        String newCoachNames = "";
        int i = 0;
        for (String CoachId : newCoachIds) {
            LoginUser coach = loginUserRepository.findById(CoachId).orElse(null);
            if (coach != null){
                if (i == 0){
                    newCoachNames = newCoachNames + coach.getName();
                } else {
                    newCoachNames = newCoachNames + "、" + coach.getName();
                }
                i++;
            }
        }
        //创建新消息
        News news = new News();
        news.setNewsType(NewsType.changeCoach);
        news.setHandleUserId(admin.getId());
        news.setHandleTime(new Date());
        news.setHandle_result(1);
        news.setReceiveLoginUser(userAndCoach.getUser());
        news.setCreateLoginUser(admin);
        news.setEntityId(id);
        news.setNewsBody("教练"+newCoachNames+"更换为您的新教练");
        news.setOrganization(organization);
        /*JSONObject json = new JSONObject();
        json.put("newCoachId",newCoachId);
        String oldCoachName = userAndCoach.getCoach().getName();
        json.put("oldCoach",oldCoachName);
        news.setContent(json.toJSONString());*/
        newsService.createNews(news,admin);

        userCoachHistoryService.save(user.getId(),newCoachId,organization.getId(),userAndCoach.getCoach().getId());
    }

    @PreAuthorize("isAuthenticated() && (hasAuthority('COACH') || hasAnyAuthority('ADMIN_ORGANIZATION'))")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "解除教练与用户")
    @RequestMapping(value = "api/fitness/relieve/coach", method = RequestMethod.POST)
    void relieveUserAndCoach(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam("id") String id
    ) throws FrogException {
        LoginUser admin = frogUserDetails.getLoginUser(loginUserRepository);
        UserAndCoach userAndCoach = userAndCoachRepository.findById(id).orElse(null);
        if(null == userAndCoach){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"数据不存在");
        }
        Organization organization = userAndCoach.getOrganization();
        //用户存在未销课的课程时不能与教练解约
        LoginUser user = userAndCoach.getUser();
        if(appointmentRepository.findNotFinish(user.getId(),organization.getId()) > 0){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"存在未销课程，无法解约");
        }
        userAndCoach.setStatus(3);
        userAndCoach = userAndCoachRepository.save(userAndCoach);
        //创建新消息
        News news = new News();
        news.setNewsType(NewsType.unviteUser);
        news.setReceiveLoginUser(userAndCoach.getUser());
        news.setCreateLoginUser(admin);
        news.setEntityId(id);
        news.setNewsBody("正在为您解约教练");
        news.setOrganization(organization);
        JSONObject json = new JSONObject();
        json.put("coachId",userAndCoach.getCoach().getId());
        json.put("coachName",userAndCoach.getCoach().getName());
        news.setContent(json.toJSONString());
        newsService.createNews(news,admin);
    }


    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "用户获取机构列表")
    @RequestMapping(value = "api/fitness/orglist", method = RequestMethod.GET)
    List<UserAndCoach> findByUser(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        List<UserAndCoach> list = userAndCoachRepository.findByUser(frogUserDetails.getLoginUserId());
        list.forEach(userAndCoach -> {
            if(4 == userAndCoach.getStatus() || 0 == userAndCoach.getStatus()){
                //userAndCoach.setCoach(null);
            }else {
                String headCoachIds = userAndCoach.getHeadCoachIds();
                List<LoginUserAuthority> loginUserAuthorityList = new ArrayList<>();
                if (!StringUtils.isEmpty(headCoachIds)) {
                    String[] coachIds = headCoachIds.split(",");
                    for (String coachId : coachIds) {
                        LoginUser coach = loginUserRepository.findById(coachId).orElse(null);
                        if (coach != null) {
                            LoginUserAuthority loginUserAuthority = loginUserAuthorityRepository.findByAuthorityAndEntityIdAndLoginUser(Authority.COACH, userAndCoach.getOrganization().getId(), coach);
                            if (loginUserAuthority == null) {
                                loginUserAuthority = loginUserAuthorityRepository.findByAuthorityAndEntityIdAndLoginUser(Authority.ADMIN_ORGANIZATION, userAndCoach.getOrganization().getId(), coach);
                            }
                            loginUserAuthorityList.add(loginUserAuthority);
                        }

                    }
                }
                List<LoginUser> coachList = new ArrayList<>();
                if(null!=loginUserAuthorityList && loginUserAuthorityList.size() > 0) {
                    for (LoginUserAuthority loginUserAuthority : loginUserAuthorityList) {
                        LoginUser coach = loginUserAuthority.getLoginUser();
                        if (coach != null){
                            coach.getProperties().put("recodeTime",loginUserAuthority.getCreateTime());
                            coachList.add(coach);
                        }
                    }
                }
                userAndCoach.getProperties().put("coachObj", coachList);
            }
            userAndCoach.getProperties().put("organizationObj",userAndCoach.getOrganization());
        });
        return list;
    }


    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN_ORGANIZATION') || hasAuthority('COACH'))")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "教练学员数")
    @RequestMapping(value = "api/fitness/coach/count", method = RequestMethod.GET)
    int countByOrgAndcoach(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam String organizationId
    ) throws FrogException {
        int num = userAndCoachRepository.countByOrgAndcoach(organizationId,frogUserDetails.getLoginUserId());
        return num;
    }


    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN_ORGANIZATION') || hasAuthority('COACH'))")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "修改用户备注")
    @RequestMapping(value = "api/fitness/user/remark", method = RequestMethod.POST)
    void modifyUserremark(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam String id,@RequestParam String remark,
            @RequestParam(value = "status",required = false) Integer status
    ) throws FrogException {
        userAndCoachRepository.findById(id).ifPresent(userAndCoach -> {
            if(status!=null){
                userAndCoach.setStatus(status);
            }
            if(!StringUtils.isEmpty(remark)){
                userAndCoach.setRemarkUserName(remark);
            }
            userAndCoachRepository.save(userAndCoach);
        });
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Api, RuntimeDoc.Client.Console}, desc = "教练签约人数")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/fitness/numberOfCoachesSigned", method = RequestMethod.GET)
    public Map<String, Integer> signatoryCount(
            @RequestParam(value = "organizationId") String organizationId,
            @RequestParam(value = "coachId", required = false, defaultValue = "") String coachId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        Map<String, Integer> map = userAndCoachService.signatoryCount(organizationId,coachId);
        return map;
    }

    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN_ORGANIZATION'))")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "标记用户状态")
    @RequestMapping(value = "api/fitness/user/signstatus/{id}", method = RequestMethod.POST)
    void  signUserStatus(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @PathVariable("id") String id,
            @RequestParam Integer userStatus
    ) throws FrogException {
        userAndCoachRepository.findById(id).ifPresent(userAndCoach -> {
            if(userStatus!=null){
                userAndCoach.setUserStatus(userStatus);
            }
            userAndCoachRepository.save(userAndCoach);
        });
    }


    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN_ORGANIZATION'))")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "用户换教练记录")
    @RequestMapping(value = "api/fitness/userCoachHistory", method = RequestMethod.GET)
    List<UserCoachHistory>  findByUserIdAndOrganizationId(@RequestParam(name = "userId")String userId,
                                                          @RequestParam(name = "orgId")String orgId)throws FrogException{
        return userCoachHistoryService.findByUserIdAndOrganizationId(userId,orgId);
    }
}

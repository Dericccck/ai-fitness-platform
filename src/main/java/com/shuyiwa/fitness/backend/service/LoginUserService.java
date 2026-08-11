package com.shuyiwa.fitness.backend.service;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.backend.channel.ChannelRepository;
import com.shuyiwa.fitness.backend.conf.CacheRedisConf;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.domain.dict.Sex;
import com.shuyiwa.fitness.backend.event.LoginUserActivityEvent;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.ContestantType;
import com.shuyiwa.fitness.backend.event.LoginUserCreatedEvent;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.util.Md5Util;
import com.shuyiwa.fitness.backend.web.Const;
import org.apache.commons.lang.StringUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.event.EventListener;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import javax.persistence.EntityManager;
import javax.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.time.Duration;
import java.util.*;
import java.util.stream.Collectors;

@Service
public class LoginUserService {
    private static final Log logger = LogFactory.getLog(LoginUserService.class);
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;
    @Autowired
    OrganizationRepository organizationRepository;
    @Autowired
    EntityManager entityManager;
    @Autowired
    ContestantInfoRepository contestantInfoRepository;
    @Autowired
    PageService pageService;
    @Autowired
    private ApplicationEventPublisher applicationEventPublisher;
    @Autowired
    private ContestSeasonRepository contestSeasonRepository;
    @Autowired
    PhoneVerifyCodeRepository phoneVerifyCodeRepository;

    @Transactional(rollbackFor = Throwable.class)
    @EventListener
    public void onLoginSuccessEvent(LoginUserActivityEvent event) {
        LoginUser loginUser = Optional.ofNullable(event.getLoginUserId()).flatMap(loginUserRepository::findById).orElse(null);
        if (loginUser != null) {
            String ua = event.getUa();
            if (ua != null) {
                ua = StringUtils.substring(ua, 0, 1000);
            }
            //TODO: 有些老版本在启动时不会传递version，所以只有当version不为空时修改，未来都需要修改
            if (event.getVersion() == null) {
                loginUserRepository.updateLastLoginTime(loginUser.getId(), event.getIp(), ua);
            } else {
                loginUserRepository.updateLastLoginInfo(loginUser.getId(), event.getVersion(), event.getIp(), ua);
            }

        }
    }

    public boolean isApplied(Optional<LoginUser> loginUserOptional, Optional<ContestSeason> contestSeasonOptional) {
        Boolean isApplied = loginUserOptional.map(loginUser ->
                contestSeasonOptional.map(contestSeason ->
                        contestantInfoRepository.findByAgentLoginUserAndContestSeasonAndDeleted(loginUser, contestSeason, false).stream()
                                .filter(contestantInfo -> contestantInfo.getContestantType() != ContestantType.ORG_VIRTUAL)
                                .filter(contestantInfo -> contestantInfo.getContestantType() != ContestantType.GROUP)//lizif要求，领队只是联系信息，领队在客户端看不到组队的作品信息
                                .count() > 0
                ).orElse(false)
        ).orElse(false);
        loginUserOptional.ifPresent(loginUser -> logger.info("isApplied:" + isApplied + ",phone:" + loginUser.getPhone() + ",contestSeason:" + contestSeasonOptional.map(ContestSeason::getId).orElse("")));
        return isApplied;
    }

    public boolean isApplied(LoginUser loginUser) {
        return contestSeasonRepository.findById(Const.defaultSeasonId).map(contestSeason -> isApplied(Optional.ofNullable(loginUser), Optional.ofNullable(contestSeason))).orElse(false);
    }

    @Transactional
    public LoginUser createLoginUser(String phone,String wxopenId) {
        return getOrCreateLoginUser(phone, false,wxopenId);
    }

    @Transactional
    public LoginUser getOrCreateLoginUser(String phone, Boolean manager,String wxopenId) {
        return loginUserRepository.findByPhone(phone).orElseGet(() -> {
            LoginUser loginUser = new LoginUser();
            loginUser.setPhone(phone);
            loginUser.setEnabled(true);
            loginUser.setLastVotesAssignTime(new Date());
            loginUser.setManager(manager);
            loginUser.setName(randomName());
            loginUser.setAvatar(randomAvatar());
            loginUser.setWeiXinOpenId(wxopenId);
            loginUser.setSex(Sex.SECRET);
            //loginUser.setPassword(Md5Util.string2MD5("123456"));
            LoginUser newLoginUser = loginUserRepository.save(loginUser);
            if (loginUser.getId() == null) {
                logger.warn("login user id is null:" + phone);
                loginUser = newLoginUser;
            }
            LoginUserCreatedEvent event = new LoginUserCreatedEvent();
            event.setLoginUser(loginUser);
            applicationEventPublisher.publishEvent(event);
            return loginUser;
        });
    }

    /**
     * 判断微信绑定的手机号与当前手机号是否相同，不同则替换
     * @param phone
     * @param wxopenId
     */
    @Transactional(rollbackFor = Throwable.class)
    public LoginUser findAndCreateUserByWeiXinOpenId(String phone,String wxopenId) {
        if(StringUtils.isEmpty(wxopenId)||StringUtils.isEmpty(phone))return null;
        LoginUser dbLoginUser = loginUserRepository.findByWeiXinOpenId(wxopenId).orElseGet(()->{
            LoginUser loginUser = loginUserRepository.findByPhone(phone).orElse(null);
            if(loginUser == null) {
                loginUser = new LoginUser();
                loginUser.setPhone(phone);
                loginUser.setEnabled(true);
                loginUser.setLastVotesAssignTime(new Date());
                loginUser.setManager(false);
                loginUser.setName(randomName());
                loginUser.setAvatar(randomAvatar());
                loginUser.setWeiXinOpenId(wxopenId);
                loginUser.setSex(Sex.SECRET);

                 loginUser = loginUserRepository.save(loginUser);
                if (loginUser.getId() == null) {
                    logger.warn("login user id is null:" + phone);
                    //loginUser = newLoginUser;
                }
                LoginUserCreatedEvent event = new LoginUserCreatedEvent();
                event.setLoginUser(loginUser);
                applicationEventPublisher.publishEvent(event);
            }else{
                loginUser.setWeiXinBindTime(new Date());
                loginUser.setWeiXinOpenId(wxopenId);
                loginUserRepository.save(loginUser);
            }
            return loginUser;
        });

        if(!dbLoginUser.getPhone().equals(phone)){
            loginUserRepository.findByPhone(phone).orElseGet(() -> {
                dbLoginUser.setPhone(phone);
                loginUserRepository.save(dbLoginUser);
                return dbLoginUser;
            });
        }
        return dbLoginUser;
    }



    public String randomAvatar() {
       /* String[] avatars = new String[]{
                "https://img.shuyiwa.com/static/header/1.png",
                "https://img.shuyiwa.com/static/header/2.png",
                "https://img.shuyiwa.com/static/header/3.png",
                "https://img.shuyiwa.com/static/header/4.png",
                "https://img.shuyiwa.com/static/header/5.png",
                "https://img.shuyiwa.com/static/header/6.png",
                "https://img.shuyiwa.com/static/header/7.png",
                "https://img.shuyiwa.com/static/header/8.png",
                "https://img.shuyiwa.com/static/header/9.png",
                "https://img.shuyiwa.com/static/header/10.png",
                "https://img.shuyiwa.com/static/header/11.png",
                "https://img.shuyiwa.com/static/header/12.png",
        };
        if (avatars.length > 0) {
            Random random = new Random();
            return avatars[random.nextInt(avatars.length)];
        }*/
        return "https://img.shuyiwa.com/fitness/header.png";
    }

    public String randomName() {
        Random random = new Random();
        StringBuilder codeBuilder = new StringBuilder();
        for (int i = 0; i < 6; i++) {
            codeBuilder.append(random.nextInt(10));
        }
        String code = codeBuilder.toString();
        String name = "F" + code;
        if (loginUserRepository.findByName(name).size() > 0) {
            return randomName();
        }
        return name;
    }

    @Transactional
    public void deleteLoginUser(String loginUserId) throws FrogException {
        LoginUser loginUser = loginUserRepository.findById(loginUserId).orElse(null);
        if (loginUser == null) {
            throw new FrogException(FrogException.LOGINUSER_NO_EXIST, "用户不存在!");
        }
        deleteUserAuthority(loginUserId);
        loginUser.setManager(false);

        loginUserRepository.save(loginUser);
    }

    @Transactional
    public LoginUser createLoginUser(LoginUser loginUser) throws FrogException {
        LoginUser dbUser = loginUserRepository.findByPhone(loginUser.getPhone()).orElse(null);
        if (dbUser != null && !dbUser.getId().equals(loginUser.getId())) {
            throw new FrogException(FrogException.LOGINUSER_PHONE_EXIST, "手机号已存在!");
        }

        Map<String, Object> properties = loginUser.getProperties();
        if (!StringUtils.isEmpty(loginUser.getId())) {
            dbUser = loginUserRepository.findById(loginUser.getId()).orElse(null);
            dbUser.setName(loginUser.getName());
            dbUser.setPhone(loginUser.getPhone());
            dbUser.setManager(true);
            loginUserRepository.save(dbUser);

        } else {
            if (!StringUtils.isEmpty(loginUser.getIdCard())){
               if (loginUser.getIdCard().length() != 15 || loginUser.getIdCard().length() != 18){
                   throw new FrogException(FrogException.LOGINUSER_PHONE_EXIST, "身份证号必须是15位或18位!");
               }
            }
            loginUser.setManager(true);
            loginUser = loginUserRepository.save(loginUser);
            {
                //刷新创建时间
                entityManager.flush();
                entityManager.refresh(loginUser);

            }
        }

        //删除修改前的权限
        deleteUserAuthority(loginUser.getId());

        if (isNotEmpty(properties.get("authority"))) {
            String[] authorities = properties.get("authority").toString().split(",");
            for (String authorityStr : authorities) {
                Authority authority = Authority.valueOf(authorityStr);

                if (Authority.ADMIN_ORGANIZATION == authority) {
                    if (isNotEmpty(properties.get("adminOrganizationList"))) {
                        for (String organizationId : properties.get("adminOrganizationList").toString().split(",")) {
                            LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
                            loginUserAuthority.setAuthority(authority);
                            loginUserAuthority.setLoginUser(loginUser);
                            loginUserAuthority.setEntityId(organizationId);
                            loginUserAuthorityRepository.save(loginUserAuthority);
                        }
                    }

                } else if (Authority.SUPER_ADMIN_ORGANIZATION == authority) {
                    if (isNotEmpty(properties.get("superAdminOrganizationList"))) {
                        for (String organizationId : properties.get("superAdminOrganizationList").toString().split(",")) {
                            if (organizationRepository.findById(organizationId).isPresent()) {
                                LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
                                loginUserAuthority.setAuthority(authority);
                                loginUserAuthority.setLoginUser(loginUser);
                                loginUserAuthority.setEntityId(organizationId);
                                loginUserAuthorityRepository.save(loginUserAuthority);
                            }

                        }
                    }
                } else if (Authority.CONTEST_OP == authority) {
                    if (isNotEmpty(properties.get("operationContestSeasonList"))) {
                        for (String contestSeasonId : properties.get("operationContestSeasonList").toString().split(",")) {
                            if (contestSeasonRepository.findById(contestSeasonId).isPresent()) {
                                LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
                                loginUserAuthority.setAuthority(authority);
                                loginUserAuthority.setLoginUser(loginUser);
                                loginUserAuthority.setEntityId(contestSeasonId);
                                loginUserAuthorityRepository.save(loginUserAuthority);
                            }
                        }
                    }
                } else if (Authority.ADMIN_CHANNEL == authority) {
                    if (isNotEmpty(properties.get("adminChannelList"))) {
                        for (String channelId : properties.get("adminChannelList").toString().split(",")) {
                            if (channelRepository.findById(channelId).isPresent()) {
                                LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
                                loginUserAuthority.setAuthority(authority);
                                loginUserAuthority.setLoginUser(loginUser);
                                loginUserAuthority.setEntityId(channelId);
                                loginUserAuthorityRepository.save(loginUserAuthority);
                            }
                        }
                    }
                } else {
                    LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
                    loginUserAuthority.setAuthority(authority);
                    loginUserAuthority.setLoginUser(loginUser);
                    loginUserAuthorityRepository.save(loginUserAuthority);
                }
            }
        }

        addAuthority(loginUser);
        return loginUser;
    }

    /**
     * 添加当前用户为机构高级管理员
     * @param loginUser
     * @param entityId
     */
    @Transactional
    public void addSuperadminOrganization(LoginUser loginUser,String entityId,String inEntityNickname){
        LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
        loginUserAuthority.setAuthority(Authority.SUPER_ADMIN_ORGANIZATION);
        loginUserAuthority.setLoginUser(loginUser);
        loginUserAuthority.setEntityId(entityId);
        loginUserAuthority.setInEntityNickname(inEntityNickname);
        loginUserAuthorityRepository.save(loginUserAuthority);
    }


    //删除某个机构管理员的权限
    @Transactional
    public void  delAdminOrgranition(String loginUserId,String entityId){
         loginUserAuthorityRepository.deleteByLoginUserIdAndEntityId(loginUserId,entityId);
    }



    @Autowired
    ChannelRepository channelRepository;

    private boolean isNotEmpty(Object obj) {
        return obj != null && !"".equals(obj);
    }

    public Page<LoginUser> search(String searchStr, int page, int size) {
        Page<LoginUser> loginUsers = loginUserRepository.findByNameAndPhone(searchStr, PageRequest.of(page, size));
//        loginUsers.stream().filter(e -> e.getManager() != null && e.getManager() == true).forEach(loginUser -> addAuthority(loginUser));
        loginUsers.stream().forEach(loginUser -> addAuthority(loginUser));
        return loginUsers;
    }

    public Page<LoginUser> hanxuqiangSearch(String searchStr, int page, int size) {
        Page<LoginUser> loginUsers = null;
        if (StringUtils.isEmpty(searchStr)) {
            loginUsers = loginUserRepository.findByIsManager(true, PageRequest.of(page, size, Sort.by("createTime").descending()));
        } else {
            loginUsers = loginUserRepository.findByNameAndPhone(searchStr, PageRequest.of(page, size));
        }
        if (loginUsers == null || loginUsers.getSize() == 0) {
            return loginUsers;
        }
//        loginUsers.stream().filter(e -> e.getManager() != null && e.getManager() == true).forEach(loginUser -> addAuthority(loginUser));
        loginUsers.stream().forEach(loginUser -> addAuthority(loginUser));
        return loginUsers;
    }

    public LoginUser searchByPhone(String phone) {
        LoginUser loginUser = loginUserRepository.findByPhone(phone).orElse(null);

        addAuthority(loginUser);
        return loginUser;
    }

    private void addAuthority(LoginUser loginUser) {
        if (loginUser == null) {
            return;
        }
        List<LoginUserAuthority> loginUserAuthorities = loginUserAuthorityRepository.findByLoginUser_IdOrderByAuthorityAsc(loginUser.getId());
        if (loginUserAuthorities != null && loginUserAuthorities.size() > 0) {
            loginUserAuthorities.stream().forEach(authhority -> {
                if (Authority.ADMIN_ORGANIZATION == authhority.getAuthority()) {
                    Organization organization = organizationRepository.findById(authhority.getEntityId()).orElse(null);
                    if (organization != null) {
                        Map<String, String> orgMap = new HashMap<>();
                        orgMap.put("id", organization.getId());
                        orgMap.put("name", organization.getName());
                        authhority.setProperty("organization", orgMap);
                    }
                }
                if (Authority.SUPER_ADMIN_ORGANIZATION == authhority.getAuthority()) {
                    Organization organization = organizationRepository.findById(authhority.getEntityId()).orElse(null);
                    if (organization != null) {
                        Map<String, String> orgMap = new HashMap<>();
                        orgMap.put("id", organization.getId());
                        orgMap.put("name", organization.getName());
                        authhority.setProperty("superAdminOrganization", orgMap);
                    }
                }
            });
        }
        loginUser.setProperty("contestSeasonList", loginUserAuthorities.stream()
                .filter(a -> a.getAuthority() == Authority.CONTEST_OP)
                .map(LoginUserAuthority::getEntityId).filter(this::isNotEmpty)
                .map(contestSeasonRepository::findById).filter(Optional::isPresent).map(Optional::get)
                .collect(Collectors.toList())
        );
        loginUser.setProperty("loginUserAuthority", loginUserAuthorities);
    }

    private void deleteUserAuthority(String loginUserId) {
        List<LoginUserAuthority> loginUserAuthorities = loginUserAuthorityRepository.findByLoginUser_IdOrderByAuthorityAsc(loginUserId);
        if (loginUserAuthorities != null && loginUserAuthorities.size() > 0) {
            loginUserAuthorityRepository.deleteAll(loginUserAuthorities);
        }
    }

    public void sendLoinUserActivityEvent(HttpServletRequest request, FrogUserDetails frogUserDetails) {
        try {
            String version = request.getParameter("version");
            String ua = request.getHeader("User-Agent");
            String ip = Optional.ofNullable(request.getHeaders("X-Forwarded-For"))
                    .map(x -> Collections.list(x).stream().reduce((first, second) -> second).orElse(null))
                    .orElse(null);
            logger.info("user:activity:event:version:" + version + ",ua:" + ua + ",ip:" + ip +
                    ",phone:" + Optional.ofNullable(frogUserDetails)
                    .map(f -> f.getLoginUser(loginUserRepository))
                    .map(LoginUser::getPhone)
                    .orElse("")
            );

            LoginUserActivityEvent event = new LoginUserActivityEvent();
            event.setLoginUserId(Optional.ofNullable(frogUserDetails).map(f -> f.getLoginUser(loginUserRepository)).map(LoginUser::getId).orElse(null));
            event.setIp(ip);
            event.setUa(ua);
            event.setVersion(version);
            applicationEventPublisher.publishEvent(event);
        } catch (Exception e) {
            logger.info("user:activity:event:", e);
        }
    }

    @Autowired
    ContestScheduleRepository contestScheduleRepository;

    @Transactional
    public void bindWeiXin(String info, String loginUserId) throws IOException, FrogException {
        ObjectMapper mapper = new ObjectMapper();
        mapper.configure(JsonParser.Feature.ALLOW_UNQUOTED_FIELD_NAMES, true);
        mapper.configure(JsonParser.Feature.ALLOW_SINGLE_QUOTES, true);
        Map<String, Object> map = mapper.readValue(info, Map.class);
        Object unionid = map.get("unionid");
        Object openId = map.get("openid");

        Optional<LoginUser> loginUserOptional = loginUserRepository.findById(loginUserId);
        if (loginUserOptional.isPresent()) {
            LoginUser loginUser = loginUserOptional.get();
            if (unionid instanceof String) {
                loginUser.setWeiXinUnionId((String) unionid);
            } else {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "weixin unionid is empty");
            }
            if (openId instanceof String) {
                loginUser.setWeiXinOpenId((String) openId);
            } else {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "weixin openId is empty");
            }
            loginUser.setWeiXinInfo(info);
            loginUser.setWeiXinBindTime(contestScheduleRepository.cachedNow());
            loginUserRepository.save(loginUser);
        } else {
            logger.warn("cannot find user:" + loginUserId);
        }
    }

    public void increaseDailyVotes(Optional<LoginUser> loginUserOptional, int rewardDailyVotes) {
        loginUserOptional.map(LoginUser::getId).flatMap(loginUserRepository::findById).ifPresent(loginUser -> {
            loginUser.setDailyVotes(loginUser.getDailyVotes() + rewardDailyVotes);
            loginUserRepository.save(loginUser);
        });
    }

    @Transactional
    public void unbindWeiXin(String loginUserId) throws FrogException {
        Optional<LoginUser> loginUserOptional = loginUserRepository.findById(loginUserId);
        if (loginUserOptional.isPresent()) {
            LoginUser loginUser = loginUserOptional.get();
            Date now = contestScheduleRepository.cachedNow();
            if (loginUser.getWeiXinBindTime() != null) {
                if (now.getTime() - loginUser.getWeiXinBindTime().getTime() < Duration.ofDays(1).toMillis()) {
                    //如果绑定不到一天 ，则不允许解绑
                    throw new FrogException(FrogException.WEI_XIN_UNBIND_TOO_QUICK, "解绑太频繁");
                }
            }
            loginUser.setWeiXinOpenId(null);
            loginUser.setWeiXinInfo(null);
            loginUser.setWeiXinUnionId(null);
            loginUserRepository.save(loginUser);
            return;
        }
    }

    @Cacheable(value = CacheRedisConf.S60, keyGenerator = CacheRedisConf.METHOD)
    public SearchResult searchUser(String search, int ot, int nt, int limit) throws FrogException {
        PageRequest pageRequest = pageService.getPage(ot, nt, limit);
//        Specification<LoginUser> searchCondition = (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("name"), "%" + search + "%");
//        Page<LoginUser> page = loginUserRepository.findAll(Specification
//                        .where(searchCondition)
//                , pageRequest);
        Page<LoginUser> page = loginUserRepository.searchByNameOrContestantName(search, pageRequest);
        SearchResult result = new SearchResult();
        result.setContent(page.getContent());
        result.setPageNumber(pageRequest.getPageNumber());
        return result;
    }

    @Transactional(rollbackFor = Throwable.class)
    public void changePassword(String newPassword, String verifyCode, String phone) {
        Optional<PhoneVerifyCode> verifyCodeOptional = phoneVerifyCodeRepository.findPhoneVerifyCode(phone, 300);
        if (verifyCodeOptional.isPresent()) {
            String verifyCodeDB = verifyCodeOptional.get().getCode();
            if (verifyCode.equals(verifyCodeDB)){
                Integer count = loginUserRepository.changePasswordByPhone(phone,Md5Util.string2MD5(newPassword));
                if (count < 1){
                    throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "修改密码失败");
                }
            }else {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "验证码错误");
            }
        } else {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "请发送验证码");
        }
    }
//    @Autowired
//    UserDailyAvailableVoteRepository userDailyAvailableVoteRepository;
//
//    @Transactional
//    public void increaseDailyAvailableVote(LoginUser loginUser, String contestSeasonId, String memo, Date startTime, Date endTime, int value){
//        UserDailyAvailableVote userDailyAvailableVote = new UserDailyAvailableVote();
//        Optional.ofNullable(contestSeasonId).flatMap(contestSeasonRepository::findById).ifPresent(contestSeason -> userDailyAvailableVote.setContestSeason(contestSeason));
//        Optional.ofNullable(memo).ifPresent(v -> userDailyAvailableVote.setMemo(v));
//        userDailyAvailableVote.setStartTime(startTime);
//        userDailyAvailableVote.setEndTime(endTime);
//        userDailyAvailableVote.setValue(value);
//        userDailyAvailableVote.setLoginUser(loginUser);
//        userDailyAvailableVoteRepository.save(userDailyAvailableVote);
//    }

    public static class SearchResult {
        private List<LoginUser> content;
        private int pageNumber;

        public List<LoginUser> getContent() {
            return content;
        }

        public void setContent(List<LoginUser> content) {
            this.content = content;
        }

        public int getPageNumber() {
            return pageNumber;
        }

        public void setPageNumber(int pageNumber) {
            this.pageNumber = pageNumber;
        }
    }
}

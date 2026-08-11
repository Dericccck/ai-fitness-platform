package com.shuyiwa.fitness.backend.sec;

import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.service.OrganizationService;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.service.LoginUserService;
import com.shuyiwa.fitness.backend.third.weixin.service.ShareService;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;

import javax.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;

@Controller
@RequestMapping("api/sec")
public class SecController {
    private static final Log logger = LogFactory.getLog(SecController.class);
    @Autowired
    SecService secService;
    @Autowired
    OrganizationRepository organizationRepository;
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    MenuService menuService;
    @Autowired
    LoginUserService loginUserService;
    @Autowired
    OrganizationService organizationService;
    @Autowired
    ShareService shareService;


    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "loginUser", method = RequestMethod.PUT)
    @ResponseBody
    @Transactional(rollbackFor = Throwable.class)
    public LoginUser updateLoginUser(
            @RequestParam(value = "avatarFileId", required = false) String avatarFileId,
            @RequestBody(required = false) LoginUser loginUser,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        return secService.modifyUserBySelf(frogUserDetails.getLoginUser(loginUserRepository).getId(), avatarFileId, loginUser);
    }

    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN_ORGANIZATION'))")
    @RequestMapping(value = "loginUser/{id}", method = RequestMethod.PUT)
    @ResponseBody
    @Transactional(rollbackFor = Throwable.class)
    public LoginUser updateLoginUserById(
            @PathVariable(value = "id")String id,
            @RequestParam(value = "avatarFileId", required = false) String avatarFileId,
            @RequestBody(required = false) LoginUser loginUser,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {

        return secService.modifyUserBySelf(id, avatarFileId, loginUser);
    }

    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "menu", method = RequestMethod.GET)
    @ResponseBody
    @Transactional(rollbackFor = Throwable.class)
    public List<MenuService.Menu> menus(@AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        List<MenuService.Menu> menus = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .flatMap(authority -> menuService.menus(authority).stream())
                .distinct()
                .collect(Collectors.toList());

        return menuService.addParent(menus);
    }

    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "loginUser", method = RequestMethod.GET)
    @ResponseBody
    public LoginUser getLoginUser(@AuthenticationPrincipal FrogUserDetails frogUserDetails, HttpServletRequest request) throws FrogException {
        loginUserService.sendLoinUserActivityEvent(request, frogUserDetails);
        return loginUserRepository.findById(frogUserDetails.getLoginUser(loginUserRepository).getId()).get();
    }

    @RequestMapping(value = "newImgVerifyCode")
    @ResponseBody
    public ImgVerifyCode newImgVerifyCode() throws IOException {
        return secService.newImgVerifyCode();
    }

    @RequestMapping(value = "sendVerifyCode")
    @ResponseBody
    public boolean sendVerifyCode(
            @RequestHeader("X-Forwarded-For") String[] xf,
            @RequestParam("__ip") String clientIp,
            @RequestParam("phone") String phone
    ) throws FrogException {
        //领导认为不需要验证码
//        secService.imgVerifyCodeCheck(imgVerifyCodeId,imgVerifyCodeCode);
        //最后一个是阿里云lsb添加的真实ip
        clientIp = Arrays.stream(xf).reduce((first, second) -> second).orElse(clientIp);
        secService.sendVerifyCode(clientIp, phone);
        return true;
    }

    @RequestMapping(value = "checkVerifyCode")
    @ResponseBody
    public boolean checkVerifyCode(
            @RequestParam("phone") String phone,
            @RequestParam("code") String code
    ) throws FrogException {
       return secService.checkVerifyCode(phone,code);
    }

    @PreAuthorize("isAuthenticated()")
    @RequestMapping("checkAuth")
    @ResponseBody
    boolean checkAuth() {
        return true;
    }

    @PreAuthorize("isAuthenticated()")
    @RequestMapping("getPrincipal")
    @ResponseBody
    FrogUserDetails getPrincipal(@AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        if (frogUserDetails != null) {
            boolean isAdmin = frogUserDetails.getAuthorities().stream()
                    .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                    .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                    .map(a -> a.getAuthorityEnum())
                    .filter(authority -> authority == Authority.ADMIN)
                    .count() > 0;
            frogUserDetails.setProperty("isAdmin", isAdmin);
        }
        return frogUserDetails;
    }


    @PreAuthorize("isAuthenticated()")
    @RequestMapping("organization")
    @ResponseBody
    @Transactional(rollbackFor = Throwable.class)
    public List<Organization> myOrganizations(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        return frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .filter(a -> a.getAuthorityEnum() == Authority.ADMIN_ORGANIZATION || a.getAuthorityEnum() == Authority.COACH)
                .map(a -> organizationRepository.findById(a.getEntityId()))
                .filter(organization -> organization.isPresent())
                .map(organization -> organization.get())
                .distinct()
                .map(organization -> {
                    organization.getProperties().put("registerUrl", organizationService.getOrgRegisterUrl(organization.getId()));
                    return organization;
                })
                .collect(Collectors.toList());
    }


    /**
     * 强制指定用户登出
     * @param frogUserDetails
     * @param userId
     */
    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN_ORGANIZATION'))")
    @RequestMapping("/flogout")
    void getPrincipal2(@AuthenticationPrincipal FrogUserDetails frogUserDetails,@RequestParam(name = "userId") String userId) {
        /*if (frogUserDetails != null) {
            boolean isAdmin = frogUserDetails.getAuthorities().stream()
                    .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                    .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                    .map(a -> a.getAuthorityEnum())
                    .filter(authority -> authority == Authority.ADMIN)
                    .count() > 0;
            frogUserDetails.setProperty("isAdmin", isAdmin);
        }*/
        secService.forceLogout(userId);
    }

    @PreAuthorize("isAuthenticated()")
    @PostMapping("/relogin")
    @ResponseBody
    public JSONObject relogin(@AuthenticationPrincipal FrogUserDetails frogUserDetails,
                              @RequestParam(value = "__ip",required = false) String clientIp){
        if(frogUserDetails.getAuthorities().isEmpty()){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"无权操作");
        }
        String userId = frogUserDetails.getLoginUserId();
        secService.forceLogout(userId);
        String phone = frogUserDetails.getPhone();
        String code = shareService.getVercode(phone,clientIp);
        String channel = "fitness-console";
        RestTemplate restTemplate = new RestTemplate();
        JSONObject json = new JSONObject();
        json.put("phone", phone);
        json.put("code", code);
//        json.put("remember-me", "on");
        json.put("channel", channel);
        HttpHeaders headers = new HttpHeaders();

        headers.setContentType(MediaType.APPLICATION_JSON);
        HttpEntity formEntity = new HttpEntity(json, headers);
        ResponseEntity<String> resp = restTemplate.postForEntity("http://localhost:8599/login",formEntity,String.class);
        List<String> cookie = resp.getHeaders().get("Set-Cookie");
//        System.out.println(cookie);
        //System.out.println(resp.getBody());
        JSONObject body=JSONObject.parseObject(resp.getBody());
        if(200==body.getInteger("code")){
            JSONObject data = body.getJSONObject("data");
            data.put("cookie",cookie);
            return data;
        }else{
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"更新失败");
        }

    }

//    @PreAuthorize("isAuthenticated()")
//    @GetMapping("/resetAuths")
//    public void resetAuthorities(@RequestParam(name = "userId") String userId){
//
//    }

    public void resetAuthorities(Long userId, List<GrantedAuthority> authorities){
        /*FrogAuthenticationToken newToken = new FrogAuthenticationToken(userId, null, authorities);
        Map<String, S> redisSessionMap = sessionRepository.findByPrincipalName(String.valueOf(userId));
        redisSessionMap.values().forEach(session -> {
            SecurityContextImpl securityContext = session.getAttribute(HttpSessionSecurityContextRepository.SPRING_SECURITY_CONTEXT_KEY);
            securityContext.setAuthentication(newToken);
            session.setAttribute(HttpSessionSecurityContextRepository.SPRING_SECURITY_CONTEXT_KEY, securityContext);
            sessionRepository.save(session);
        });*/
    }


}

package com.shuyiwa.fitness.backend.web;

import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.LoginUser;
import com.shuyiwa.fitness.backend.domain.LoginUserRepository;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.service.LoginUserService;
import com.shuyiwa.fitness.backend.service.PageService;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@RestController
public class LoginUserController {

    private static final Log logger = LogFactory.getLog(LoginUserController.class);
    @Autowired
    LoginUserService loginUserService;

    @Autowired
    PageService pageService;
    @Autowired
    LoginUserRepository loginUserRepository;

    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "获取指定用户基本信息")
    @RequestMapping(value = "api/login/user/summary", method = RequestMethod.GET)
    Map<String, Object> loginUserSummary(
            @RequestParam(value = "loginUserId") String loginUserId) throws FrogException {
        Map<String, Object> map = new HashMap<>();
        Optional<LoginUser> loginUser = loginUserRepository.findById(loginUserId);
        loginUser.map(LoginUser::getName).ifPresent(name -> map.put("name", name));
        loginUser.map(LoginUser::getAvatar).ifPresent(avatar -> map.put("avatar", avatar));
        return loginUser.map(a -> map).orElse(null);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "搜索用户列表")
    @RequestMapping(value = "api/login/user/app/search", method = RequestMethod.GET)
    List<LoginUser> contestSeasonList(
            @RequestParam(value = "search", required = false, defaultValue = "") String search,
            @RequestParam(value = "ot", required = false, defaultValue = "-1") int ot,
            @RequestParam(value = "nt", required = false, defaultValue = "-1") int nt,
            @RequestParam(value = "limit", required = false) int limit) throws FrogException {
        LoginUserService.SearchResult pageResult = loginUserService.searchUser(search, ot, nt, limit);
        List<LoginUser> content = pageResult.getContent();
        content.forEach(contestSeason -> contestSeason.setProperty("score", pageResult.getPageNumber() + 1));
        return content;
    }


    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "搜索用户")
    @PreAuthorize("isAuthenticated() ")
    @RequestMapping(value = "api/user/page", method = RequestMethod.GET)
    Page<LoginUser> hanxuqiangSearch(
            @RequestParam("searchStr") String searchStr,
            @RequestParam int page,
            @RequestParam int size) {
        Page<LoginUser> result = loginUserService.hanxuqiangSearch(searchStr, page, size);
        return result;
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "搜索用户")
//    @PreAuthorize("isAuthenticated() ")
    @RequestMapping(value = "api/user/search", method = RequestMethod.GET)
    Page<LoginUser> search(
            @RequestParam("search") String searchStr,
            @RequestParam int page,
            @RequestParam int size) {
        Page<LoginUser> result = loginUserService.search(searchStr, page, size);
        return result;
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "根据手机号搜索用户")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/user/searchByPhone", method = RequestMethod.GET)
    LoginUser searchByPhone(@AuthenticationPrincipal FrogUserDetails frogUserDetails, String phone) {
        LoginUser loginUser = loginUserService.searchByPhone(phone);
        return loginUser;
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "保存用户")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN') ||  hasAuthority('ADMIN_LOGIN_USER') )")
    @RequestMapping(value = "api/user/save", method = RequestMethod.POST)
    LoginUser save(@AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestBody LoginUser loginUser) throws FrogException {
        LoginUser result = loginUserService.createLoginUser(loginUser);
        return result;
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "绑定微信")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/user/bind/weixin", method = RequestMethod.POST)
    void bindWeixin(
            @RequestBody String info,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException, IOException {
        logger.info("bind weixin:" + info);
        loginUserService.bindWeiXin(info, frogUserDetails.getLoginUserId());
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "解绑微信")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/user/unbind/weixin", method = RequestMethod.POST)
    void unbindWeixin(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException, IOException {
        loginUserService.unbindWeiXin(frogUserDetails.getLoginUserId());
    }


    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "删除用户")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN') ||  hasAuthority('ADMIN_LOGIN_USER') )")
    @RequestMapping(value = "api/user/del", method = RequestMethod.DELETE)
    void del(@AuthenticationPrincipal FrogUserDetails frogUserDetails, String loginUserId) throws FrogException {
        loginUserService.deleteLoginUser(loginUserId);
        return;
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "修改密码")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN_ORGANIZATION'))")
    @RequestMapping(value = "api/user/changePassword", method = RequestMethod.POST)
    void changePassword(@AuthenticationPrincipal FrogUserDetails frogUserDetails,
                        @RequestBody JSONObject jsonObject) throws FrogException {
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        String newPassword = jsonObject.getString("newPassword");
        String verifyCode = jsonObject.getString("verifyCode");
        loginUserService.changePassword(newPassword, verifyCode, loginUser.getPhone());
    }

}

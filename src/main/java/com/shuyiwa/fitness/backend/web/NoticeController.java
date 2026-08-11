package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.service.NoticeService;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;

import java.util.*;


@RestController
public class NoticeController {
    private static final Log logger = LogFactory.getLog(NoticeController.class);
    @Autowired
    NoticeService noticeService;
    @Autowired
    NoticeContainRepository noticeContainRepository;
    @Autowired
    MenuService menuService;
    @Autowired
    LoginUserRepository loginUserRepository;


    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "搜索通知")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN_ORGANIZATION'))")
    @RequestMapping(value = "api/notice/search/page", method = RequestMethod.GET)
    Page<Notice> search(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam int page,
            @RequestParam int size) throws FrogException {
        Page<Notice> result;
        result = noticeContainRepository.findByDeleted(false, PageRequest.of(page, size, Sort.by("createTime").descending()));
        Map<String, String> menuMap = noticeService.queryMenuMap(frogUserDetails);
        result.stream().forEach(notice -> {
            String urls = notice.getUrls();
            String names = "";
            for (String url : urls.split(",")) {
                names += menuMap.get(url);
                names += "、";
            }
            notice.setProperty("menuNames", names);
        });
        return result;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "停用按钮")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN_ORGANIZATION'))")
    @RequestMapping(value = "api/notice/stop", method = RequestMethod.POST)
    Notice stop(@RequestParam String id, @AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        Notice notice = noticeContainRepository.findByNoticeId(id);
        notice.setStatus(false);
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        notice.setLastUpdateLoginUser(loginUser);
        notice.setLastUpdateTime(new Date());
        notice = noticeContainRepository.save(notice);
        return notice;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "发布按钮")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN_ORGANIZATION') )")
    @RequestMapping(value = "api/notice/publish", method = RequestMethod.POST)
    Notice publish(@RequestParam String id, @AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        Notice notice = noticeContainRepository.findByNoticeId(id);
        notice.setStatus(true);
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        notice.setLastUpdateLoginUser(loginUser);
        notice.setLastUpdateTime(new Date());
        notice = noticeContainRepository.save(notice);
        return notice;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "删除按钮")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN_ORGANIZATION') )")
    @RequestMapping(value = "api/notice/delete", method = RequestMethod.DELETE)
    void delete(@RequestParam String id) {
        noticeService.delete(id);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "新建-保存通知")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN_ORGANIZATION'))")
    @RequestMapping(value = "api/notice/save", method = RequestMethod.POST)
    Notice save(@RequestBody Notice notice, @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        Notice n1=notice;
        if(notice.getId()==null||"".equals(notice.getId())){
            notice.setCreateLoginUser(loginUser);
        }
        notice.setLastUpdateLoginUser(loginUser);
        notice.setLastUpdateTime(new Date());
        notice = noticeContainRepository.save(notice);
        return notice;
    }

    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/notice/menus", method = RequestMethod.GET)
    @ResponseBody
    @Transactional(rollbackFor = Throwable.class)
    public List<MenuService.Menu> menus(@AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestParam String systemId) throws FrogException {
        List<MenuService.Menu> menus = noticeService.menus(frogUserDetails);
//        Iterator<MenuService.Menu> iterator = menus.iterator();
        //判断系统类型
//        if(systemId.equals("console")){
//            while (iterator.hasNext()){
//                MenuService.Menu m = iterator.next();
//                String platform= String.valueOf(m.getPlatform());
////                if(platform.equals("fitness_console")){
////                    iterator.remove();
////                }
//            }
//            return menus;
//        }else if(systemId.equals("org")){
//            while (iterator.hasNext()){
//                MenuService.Menu m = iterator.next();
//                String platform= String.valueOf(m.getPlatform());
//                if(!platform.equals("frog_org")){
//                    iterator.remove();
//                }
//            }
//            return menus;
//        }
        return menus;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "根据Url搜索通知")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/org/notice/loadList", method = RequestMethod.GET)
    List<Notice> searchByUrl(@RequestParam String systemId,@RequestParam String pageUrl) throws FrogException {
        List<Notice> notices = noticeContainRepository.getAllNotDeleted();
        Iterator<Notice> I = notices.iterator();
        while (I.hasNext()){
            Notice n = I.next();
            if (!n.getSystemId().equals(systemId)||!n.getUrls().contains(pageUrl)){
                I.remove();
            }
        }
        return notices;
    }
}

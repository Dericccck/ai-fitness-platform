package com.shuyiwa.fitness.backend.web;

import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.conf.CacheRedisConf;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.NewsType;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.service.NewsService;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.data.domain.Page;;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.Date;
import java.util.List;
import java.util.Objects;

@RestController
public class NewsController {

    private static final Log logger = LogFactory.getLog(NewsController.class);

    @Autowired
    NewsService newsService;

    @Autowired
    LoginUserRepository loginUserRepository;

    @Autowired
    UserAndCoachRepository userAndCoachRepository;

    @Autowired
    OrganizationRepository organizationRepository;

    @Autowired
    NewsRepository newsRepository;

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "保存消息", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/news/save", method = RequestMethod.POST)
    void saveNews(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,@RequestBody News news
    ) throws FrogException {
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        newsService.createNews(news,loginUser);
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "更新消息", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/news/update/{id}", method = RequestMethod.POST)
    void updateNews(@PathVariable String id,@RequestParam int status,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        newsService.updateNews(id,loginUser,status);
    }



    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "查询消息", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/news/page", method = RequestMethod.GET)
    Page<News> findByPage(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,@RequestParam int page,@RequestParam int size,
            @RequestParam(required = false) String organizationId
    ) throws FrogException {
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        return newsService.findNewsByPage(page,size,loginUser.getId(),organizationId);
    }

    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "主管查询消息", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/admin/news/page", method = RequestMethod.GET)
//    @Cacheable(value = CacheRedisConf.S60, keyGenerator = CacheRedisConf.METHOD)
    Page<News> findByPageAdmin(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,@RequestParam int page,@RequestParam int size,
            @RequestParam(required = false) String organizationId,
            @RequestParam(value = "all", required = false,defaultValue = "false") boolean all,
            @RequestParam(value = "appointments", required = false,defaultValue = "false") boolean appointments,
            @RequestParam(value = "finishClass", required = false,defaultValue = "false") boolean finishClass,
            @RequestParam(value = "changeClass", required = false,defaultValue = "false") boolean changeClass,
            @RequestParam(value = "unprocessed", required = false,defaultValue = "false") boolean unprocessed,
            @RequestParam(value = "timeLimit", required = false,defaultValue = "false") boolean timeLimit
    ) throws FrogException {
        long starttime=System.currentTimeMillis();
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        Page<News> pages = newsService.findNewsByPageAdmin(page,size,loginUser.getId(),organizationId,all,appointments,finishClass,changeClass,unprocessed,timeLimit);
        logger.info("findByPageAdmin耗时："+(System.currentTimeMillis()-starttime)+"ms");
        return pages;
    }


    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "新的未处理消息条数", sinceTime = "2022-10-18")
    @RequestMapping(value = "api/admin/undoNews/count", method = RequestMethod.GET)
    Integer countUndoNews(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam String organizationId,
            @RequestParam(name = "createTime",defaultValue = "") String createTime
    ) throws FrogException {
        // LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        Integer num = 0;
        if(Objects.isNull(createTime) || "".equals(createTime)){
           num = newsRepository.countUndoNewsNoCreateTime(organizationId);
        }else{
            Date d = new Date(Long.valueOf(createTime));
            num = newsRepository.countUndoNews(organizationId,d);
        }
        if(num == null)
            num = 0;
        return num;
    }




//    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATION')")
//    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "主管查询消息2（当前活动）", sinceTime = "2021-06-01")
//    @RequestMapping(value = "api/admin/news/now/page", method = RequestMethod.GET)
//    Page<News> findByPageAdminNow(
//            @AuthenticationPrincipal FrogUserDetails frogUserDetails,@RequestParam int page,@RequestParam int size,
//            @RequestParam(required = false) String organizationId,
//            @RequestParam(value = "all", required = false,defaultValue = "false") boolean all,
//            @RequestParam(value = "appointments", required = false,defaultValue = "true") boolean appointments,
//            @RequestParam(value = "finishClass", required = false,defaultValue = "true") boolean finishClass,
//            @RequestParam(value = "changeClass", required = false,defaultValue = "false") boolean changeClass,
//            @RequestParam(value = "unprocessed", required = false,defaultValue = "false") boolean unprocessed
//    ) throws FrogException {
//        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
//        return newsService.findNewsByPageAdmin(page,size,loginUser.getId(),organizationId,all,appointments,finishClass,changeClass,unprocessed);
//    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "查询登陆人未处理消息数量", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/news/unreadNum", method = RequestMethod.GET)
    int countUnredNews(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(required = true) String organizationId
    ) throws FrogException {
//        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        return newsService.countUnredNews(frogUserDetails.getLoginUserId(),organizationId);
    }




//    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATIONS')")
//    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "通知用户更改教练待确认")
//    @RequestMapping(value = "api/fitness/news/change/coach", method = RequestMethod.POST)
//    News changeCoach(
//            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
//            @RequestParam("userAndCoachId") String userAndCoachId,
//            @RequestParam(value = "newCoachId")String newCoachId
//    ) throws FrogException {
//        LoginUser admin = frogUserDetails.getLoginUser(loginUserRepository);
//        UserAndCoach userAndCoach = userAndCoachRepository.findById(userAndCoachId).get();
//        LoginUser newCoach = loginUserRepository.findById(newCoachId).get();
//        Organization organization = organizationRepository.findById(userAndCoach.getOrganization().getId()).get();
//        News news = new News();
//        news.setNewsType(NewsType.changeCoach);
//        news.setCreateLoginUser(admin);
//        news.setReceiveLoginUser(userAndCoach.getUser());
//        news.setEntityId(userAndCoachId);
//        news.setNewsBody("正在更换"+newCoach.getName()+"为您的新教练");
//        news.setOrganization(organization);
//        JSONObject json = new JSONObject();
//        json.put("newCoachId",newCoachId);
//        news.setContent(json.toJSONString());
//        return newsRepository.save(news);
//    }


}

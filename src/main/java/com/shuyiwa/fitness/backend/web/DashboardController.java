package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.CacheRedisConf;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.global.FrogException;

import com.shuyiwa.fitness.backend.service.WorksService;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.data.domain.PageRequest;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.*;

import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.time.Duration;
import java.util.*;
import java.util.function.BiConsumer;
import java.util.stream.Collectors;

import static com.shuyiwa.fitness.backend.web.Const.diqijie;

@RestController
public class DashboardController {
    private static final Log logger = LogFactory.getLog(DashboardController.class);
    @Autowired
    UserLoginDateRepository userLoginDateRepository;
    @Autowired
    VoteRepository voteRepository;
    @Autowired
    UserLikeRepository userLikeRepository;
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    WorksRepository worksRepository;
    @Autowired
    WorksService worksService;
    @Autowired
    WorksActionMinuteRepository worksActionMinuteRepository;
    @Autowired
    OrganizationDataDayRepository organizationDataDayRepository;
    @Autowired
    OrganizationRepository organizationRepository;
    @Autowired
    ContestSeasonRepository contestSeasonRepository;


    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "机构日数据统计")
    @RequestMapping(value = "api/org/dashboard/day/data", method = RequestMethod.GET)
    @ResponseBody
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD)
    public List<Map<String, Object>> organizationHeat(
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end,
            @RequestParam("organizationId") String organizationId,
            @RequestParam("dataType") String dataType
    ) {
        return addMissDate(start, end, organizationDataDayRepository.query(start, end, organizationId, dataType), new SimpleDateFormat("yyyy-MM-dd"));
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "每日登陆用户数")
    @RequestMapping(value = "api/dashboard/user/login/date", method = RequestMethod.GET)
    @ResponseBody
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD)
    public List<Map<String, Object>> loginDate(
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end
    ) {
        return addMissDate(start, end, userLoginDateRepository.query(start, end), new SimpleDateFormat("yyyy-MM-dd"));
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "每日投票数")
    @RequestMapping(value = "api/dashboard/vote", method = RequestMethod.GET)
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD)
    @ResponseBody
    public List<Map<String, Object>> vote(
            @RequestParam(required = false, defaultValue = "") String contestSeasonId,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end
    ) {
        return addMissDate(start, end, worksActionMinuteRepository.query(contestSeasonId, WorksActionMinute.Action.vote.name(), start, new Date(end.getTime() + Duration.ofDays(1).toMillis())), new SimpleDateFormat("yyyy-MM-dd"));
    }

    @Autowired
    ContestantInfoRepository contestantInfoRepository;

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "新增用户数")
    @RequestMapping(value = "api/dashboard/login/user", method = RequestMethod.GET)
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD)
    @ResponseBody
    public List<Map<String, Object>> loginUser(
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end
    ) {
        return addMissDate(start, end, loginUserRepository.query(start, new Date(end.getTime() + Duration.ofDays(1).toMillis())), new SimpleDateFormat("yyyy-MM-dd"));
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "报名数")
    @RequestMapping(value = "api/dashboard/contestant/info", method = RequestMethod.GET)
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD)
    @ResponseBody
    public List<Map<String, Object>> contestantInfo(
            @RequestParam(required = false, defaultValue = "") String contestSeasonId,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end
    ) {
        return addMissDate(start, end, contestantInfoRepository.query(contestSeasonId, start, new Date(end.getTime() + Duration.ofDays(1).toMillis())), new SimpleDateFormat("yyyy-MM-dd"));
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "每日上传作品数")
    @RequestMapping(value = "api/dashboard/works", method = RequestMethod.GET)
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD)
    @ResponseBody
    public List<Map<String, Object>> works(
            @RequestParam(required = false, defaultValue = "") String contestSeasonId,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end
    ) {
        return addMissDate(start, end, worksRepository.query(contestSeasonId, start, new Date(end.getTime() + Duration.ofDays(1).toMillis())), new SimpleDateFormat("yyyy-MM-dd"));
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "每日点赞数")
    @RequestMapping(value = "api/dashboard/user/like", method = RequestMethod.GET)
    @ResponseBody
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD, sync = true)
    public List<Map<String, Object>> userLike(
            @RequestParam(required = false, defaultValue = "") String contestSeasonId,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end
    ) {
//        return addMissDate(start, end, userLikeRepository.query(start, new Date(end.getTime() + Duration.ofDays(1).toMillis())), new SimpleDateFormat("yyyy-MM-dd"));
        return addMissDate(start, end, worksActionMinuteRepository.query(contestSeasonId, WorksActionMinute.Action.like.name(), start, new Date(end.getTime() + Duration.ofDays(1).toMillis())), new SimpleDateFormat("yyyy-MM-dd"));
    }

    private void forEachDate(Date start, Date end, BiConsumer<Date, Date> consumer) {
        Date label = start;
        while (label.before(end) || label.equals(end)) {
            Date next = new Date(label.getTime() + Duration.ofDays(1).toMillis());
            consumer.accept(label, next);
            label = next;
        }
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "累积上传作品数")
    @RequestMapping(value = "api/dashboard/works/acc", method = RequestMethod.GET)
    @ResponseBody
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD, sync = true)
    public List<Map<String, Object>> worksAcc(
            @RequestParam(required = false, defaultValue = "") String contestSeasonId,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end
    ) {
        List<Map<String, Object>> list = new ArrayList<>();
        forEachDate(start, end, (label, next) -> {
            Map<String, Object> map = new HashMap<>();
            map.put("label", label);
            map.put("value", worksRepository.countByCreateTimeLessThanAndDeleted(next, false));
            list.add(map);
        });
        return list;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "累积投票数")
    @RequestMapping(value = "api/dashboard/vote/acc", method = RequestMethod.GET)
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD, sync = true)
    @ResponseBody
    public List<Map<String, Object>> voteAcc(
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end
    ) {
        List<Map<String, Object>> list = new ArrayList<>();
        forEachDate(start, end, (label, next) -> {
            Map<String, Object> map = new HashMap<>();
            map.put("label", label);
            map.put("value", worksActionMinuteRepository.actionTimeLessThan(WorksActionMinute.Action.vote.name(), next));
            list.add(map);
        });
        return list;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "累积点赞数")
    @RequestMapping(value = "api/dashboard/user/like/acc", method = RequestMethod.GET)
    @ResponseBody
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD, sync = true)
    public List<Map<String, Object>> userLikeAcc(
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end
    ) {
        List<Map<String, Object>> list = new ArrayList<>();
        forEachDate(start, end, (label, next) -> {
            Map<String, Object> map = new HashMap<>();
            map.put("label", label);
            map.put("value", worksActionMinuteRepository.actionTimeLessThan(WorksActionMinute.Action.like.name(), next));
            list.add(map);
        });
        return list;

    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "累积用户数")
    @RequestMapping(value = "api/dashboard/user/acc", method = RequestMethod.GET)
    @ResponseBody
    @Cacheable(value = CacheRedisConf.S3600, keyGenerator = CacheRedisConf.METHOD, sync = true)
    public List<Map<String, Object>> userAcc(
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "start") Date start,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "end") Date end
    ) {
        List<Map<String, Object>> list = new ArrayList<>();
        forEachDate(start, end, (label, next) -> {
            Map<String, Object> map = new HashMap<>();
            map.put("label", label);
            map.put("value", loginUserRepository.countByCreateTimeLessThan(next));
            list.add(map);
        });
        return list;

    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "作品投票榜")
    @RequestMapping(value = "api/dashboard/vote/works/top", method = RequestMethod.GET)
    @ResponseBody
    @Cacheable(value = CacheRedisConf.S60, keyGenerator = CacheRedisConf.METHOD)
    public List<Works> topVoteWorks(
            @RequestParam(value = "page", required = false, defaultValue = "0") int page,
            @RequestParam(value = "size", required = false, defaultValue = "10") int size
    ) {

        return worksRepository.topVoteWorksInContestSeason(diqijie, PageRequest.of(page, size)).stream()
                .map(works -> {
                    worksService.fill(works);
                    works.setProperty("count", works.getVotes());
                    return works;
                }).collect(Collectors.toList());
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "作品点赞榜")
    @RequestMapping(value = "api/dashboard/user/like/works/top", method = RequestMethod.GET)
    @Cacheable(value = CacheRedisConf.S60, keyGenerator = CacheRedisConf.METHOD)
    public List<Works> topUserLikeWorks(
            @RequestParam(value = "page", required = false, defaultValue = "0") int page,
            @RequestParam(value = "size", required = false, defaultValue = "10") int size
    ) {
        return worksRepository.topUserLikeWorksInContestSeason(diqijie, PageRequest.of(page, size)).stream()
                .map(works -> {
                    works.setProperty("count", works.getUserLikes());
                    worksService.fill(works);
                    return works;
                }).filter(works -> works != null).collect(Collectors.toList());
    }


    private List<Map<String, Object>> addMissDate(Date start, Date end, List<Map<String, Object>> list) {
        Map<Object, Object> map = list.stream().collect(Collectors.toMap(e -> e.get("label"), e -> e.get("value")));
        list.clear();
        forEachDate(start, end, (label, next) -> {
            Object value = map.getOrDefault(label, 0);
            Map<String, Object> e = new HashMap<>();
            e.put("label", label);
            e.put("value", value);
            list.add(e);
        });
        return list;
    }

    private List<Map<String, Object>> addMissDate(Date start, Date end, List<Map<String, Object>> list, SimpleDateFormat format) {
        list = list.stream().map(m -> {
            Object label = m.get("label");
            if (label instanceof String) {
                try {
                    label = format.parse((String) label);
                } catch (ParseException e) {
                    logger.warn("label format exception:" + label, e);
                }
            } else if (label instanceof Date) {
                try {
                    label = format.parse(format.format(label));
                } catch (ParseException e) {
                    logger.warn("label format exception:" + label, e);
                }
            }
            Map<String, Object> map = new HashMap<>();
            map.put("label", label);
            map.put("value", m.get("value"));
            return map;
        }).collect(Collectors.toList());
        return addMissDate(start, end, list);
    }

    @RuntimeDoc( desc = "小程序获取机构信息")
    @RequestMapping(value = "api/organization/info/mini", method = RequestMethod.GET)
    @ResponseBody
    public Map<String,Object> applyUrl(
            @RequestParam(value = "organizationId") String organizationId,
            @DateTimeFormat(pattern = "yyyy-MM-dd")
            @RequestParam(value = "date") Date date
    ) throws FrogException {
        Map<String,Object> result =new HashMap();
        Optional<Organization> org = organizationRepository.findById(organizationId);
        result.put("auditStatus",org.get().getAuditStatus());
        result.put("logo",org.get().getLogo());
        Calendar calendar = new GregorianCalendar();
        calendar.setTime(date);
        calendar.add(calendar.DATE,-1);
        Date d = calendar.getTime();
        List<Map<String,Object>> likeList = organizationDataDayRepository.query(d,d,organizationId,"like");
        if(likeList.size()>0){
            result.put("like",likeList.get(0).get("value"));
        }else {
            result.put("like",0);
        }
        List<Map<String,Object>> userList = organizationDataDayRepository.query(d,d,organizationId,"user");
        if(userList.size()>0){
            result.put("user",userList.get(0).get("value"));
        }else {
            result.put("user",0);
        }
        List<Map<String,Object>> voteList = organizationDataDayRepository.query(d,d,organizationId,"vote");
        if(voteList.size()>0){
            result.put("vote",voteList.get(0).get("value"));
        }else {
            result.put("vote",0);
        }
        int contestSeasonCount = contestSeasonRepository.appliedCount(organizationId);
        result.put("contestSeasonCount",contestSeasonCount);
        result.put("heat",org.get().getHeat());
        return result;
    }

}

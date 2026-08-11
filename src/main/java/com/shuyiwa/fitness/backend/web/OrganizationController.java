package com.shuyiwa.fitness.backend.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.backend.conf.RequestBodyPartResolverConfig;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.service.*;
import com.shuyiwa.fitness.backend.channel.ChannelRegisterService;
import com.shuyiwa.fitness.backend.domain.dict.ContestantType;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.sec.FrogUserDetailsService;
import net.sourceforge.pinyin4j.PinyinHelper;
import org.apache.commons.lang.exception.ExceptionUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.hibernate.query.criteria.internal.CriteriaBuilderImpl;
import org.hibernate.query.criteria.internal.expression.LiteralExpression;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.*;

import javax.persistence.criteria.Path;
import javax.servlet.ServletResponse;
import java.math.BigDecimal;
import java.net.MalformedURLException;
import java.net.URL;
import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.stream.Collectors;

import static com.shuyiwa.fitness.backend.Utils.injectSpace;
import static com.shuyiwa.fitness.backend.domain.Sms.Template.SMS_196619870;
import static com.shuyiwa.fitness.backend.global.FrogException.FORBIDDEN;
import static com.shuyiwa.fitness.backend.global.FrogException.INTERNAL_SERVER_ERROR;
import static com.shuyiwa.fitness.backend.web.Const.defaultSeasonId;

@RestController
public class OrganizationController {

    private static final Log logger = LogFactory.getLog(OrganizationController.class);
    @Autowired
    OrganizationRepository organizationRepository;

    //    @Autowired
//    ContestantMemberRepository contestantMemberRepository;
    @Autowired
    ContestantRepository contestantRepository;
    //    @Autowired
//    DivisionContestantRepository divisionContestantRepository;
    @Autowired
    WorksRepository worksRepository;
    @Autowired
    LoginUserFileRepository loginUserFileRepository;
    @Autowired
    OrganizationService organizationService;
    @Autowired
    WorksService worksService;
    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;
    @Autowired
    ContestSeasonRepository contestSeasonRepository;
    @Autowired
    ContestantInfoRepository contestantInfoRepository;
    @Autowired
    ContestService contestService;
    @Autowired
    ContestItemRepository contestItemRepository;
    @Autowired
    PageService pageService;
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    ChannelRegisterService channelRegisterService;
    @Autowired
    OrganizationApplicableContestSeasonRepository organizationApplicableContestSeasonRepository;
    @Autowired
    ObjectMapper objectMapper;
    @Autowired
    WarnService warnService;
    @Autowired
    SmsService smsService;
    @Autowired
    LoginUserService loginUserService;


    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "搜索机构作品")
    @RequestMapping(value = "api/org/works/search/name/page", method = RequestMethod.GET)
    Page<Works> searchWorks(
            @RequestParam("organizationId") String organizationId,
            @RequestParam(value = "search", required = false, defaultValue = "") String search,
            @RequestParam int page,
            @RequestParam int size,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {

        PageRequest pageRequest = PageRequest.of(page, size);
        if (StringUtils.isEmpty(search)) {
            pageRequest = PageRequest.of(page, size, Sort.by("updateTime").descending().and(Sort.by("id").descending()));
        }


        Specification<Works> empty = Specification.where(null);
        Specification<Works> notDeleted = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
        Specification<Works> searchCondition = Optional.ofNullable(search).map(String::trim).map(v -> StringUtils.isEmpty(v) ? null : v).map(v -> (Specification<Works>) (root, query, criteriaBuilder) ->
                criteriaBuilder.greaterThan(criteriaBuilder.function("match", Double.class, root.get("nameWithSpace"), new LiteralExpression<String>((CriteriaBuilderImpl) criteriaBuilder, injectSpace(v))), 0.)
        ).orElse(empty);

        Specification<Works> organizationCondition = Optional.ofNullable(organizationId).filter(v -> !StringUtils.isEmpty(v)).map(v -> (Specification<Works>) (root, query, criteriaBuilder) ->
                criteriaBuilder.equal(root.get("contestant").get("contestantInfo").get("organization").get("id"), v)
        ).orElse(empty);

        Page<Works> worksList = worksRepository.findAll(Specification
                        .where(notDeleted)
                        .and(searchCondition)
                        .and(organizationCondition)
                , pageRequest);
        worksList.forEach(worksService::fill);
//        worksList.forEach(worksService::consoleFill);

        return worksList;
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "搜索机构列表")
    @RequestMapping(value = "api/organization/app/search", method = RequestMethod.GET)
    List<Organization> contestSeasonList(
            @RequestParam(value = "search", required = false, defaultValue = "") String search,
            @RequestParam(value = "ot", required = false, defaultValue = "-1") int ot,
            @RequestParam(value = "nt", required = false, defaultValue = "-1") int nt,
            @RequestParam(value = "limit", required = false, defaultValue = "10") int limit,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        PageRequest pageRequest = pageService.getPage(ot, nt, limit);
        Specification<Organization> searchCondition = (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("name"), "%" + search + "%");
        Page<Organization> pageResult = organizationRepository.findAll(Specification
                        .where(searchCondition)
                , pageRequest);
        List<Organization> content = pageResult.getContent();
        content.forEach(contestSeason -> contestSeason.setProperty("score", pageRequest.getPageNumber() + 1));
        return content;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "报名赛事时可选机构列表", since = "1.2.3")
    @RequestMapping(value = "api/contest/season/applied/organization", method = RequestMethod.GET)
    List<Organization> organizationList(
            @RequestParam(value = "contestSeasonId", required = false, defaultValue = Const.defaultSeasonId) String contestSeasonId,
            @RequestParam(value = "ot", required = false, defaultValue = "-1") int ot,
            @RequestParam(value = "nt", required = false, defaultValue = "-1") int nt,
            @RequestParam(value = "limit", required = false, defaultValue = "10") int limit,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {

        Specification<OrganizationApplicableContestSeason> seasonSpecification = (root, criteriaQuery, criteriaBuilder) ->
        {
            criteriaQuery.orderBy(criteriaBuilder.asc(criteriaBuilder.function("convert", String.class, root.get("organization").get("name"), criteriaBuilder.literal("gbk"))));
            return criteriaBuilder.equal(root.get("contestSeason").get("id"), contestSeasonId);
        };
        Specification<OrganizationApplicableContestSeason> joinStateSpecification = (root, criteriaQuery, criteriaBuilder) ->
                criteriaBuilder.equal(root.get("joinState"), 1);
        Specification<OrganizationApplicableContestSeason> contestSeasonNotDeleted = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestSeason").get("deleted"), false);
        Specification<OrganizationApplicableContestSeason> contestSeasonTimeCondition = (root, query, criteriaBuilder) ->
                criteriaBuilder.between(criteriaBuilder.function("now", Date.class), root.get("contestSeason").get("startTime"), root.get("contestSeason").get("endTime"));
        Specification<OrganizationApplicableContestSeason> contestSeasonTypeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestSeason").get("contestSeasonType"), ContestSeason.ContestSeasonType.CONTEST);

        PageRequest pageRequest = pageService.getPage(ot, nt, limit);
        List<Organization> collect = organizationApplicableContestSeasonRepository.findAll(
                seasonSpecification
                        .and(joinStateSpecification)
                        .and(contestSeasonNotDeleted)
                        .and(contestSeasonTimeCondition).and(contestSeasonTypeCondition),
                pageRequest
        ).stream()
                .map(OrganizationApplicableContestSeason::getOrganization)
                .collect(Collectors.toList());

        collect.forEach(contestSeason -> contestSeason.setProperty("score", pageRequest.getPageNumber() + 1));
        return collect;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "获取机构可报名的赛事")
    @RequestMapping(value = "api/organization/applicable/contest/season", method = RequestMethod.GET)
    List<Object> applicableContestSeason(@RequestParam("organizationId") String organizationId) throws FrogException {
        return organizationRepository.findById(organizationId).map(this::getApplicableContestSeason).orElseThrow(() -> new FrogException(FORBIDDEN, ""));
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "机构是否参赛，对于老数据，根据是否有报名信息来修改")
    @RequestMapping(value = "api/tool/organization/applicable/contest/season/update", method = RequestMethod.GET)
    List<String> updateOldApplicableContestSeason() throws FrogException, ParseException {
        List<String> result = new ArrayList<>();
        Date date = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss").parse("2020-05-15T00:00:00");
        Specification<ContestantInfo> timeCondition = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.lessThan(root.get("createTime"), date);
        Specification<ContestantInfo> deleteCondition = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
        List<ContestantInfo> infoList = contestantInfoRepository.findAll(timeCondition.and(deleteCondition));
        result.add("updateOldApplicableContestSeason:infoList" + infoList.size());
        Map<String, ContestantInfo> map = new HashMap<>();
        infoList.forEach(info -> {
            if (info.getOrganization() != null && info.getContestSeason() != null) {
                map.put(info.getContestSeason().getId() + "_" + info.getOrganization().getId(), info);
            }
        });
        result.add("updateOldApplicableContestSeason:" + map.size());
        logger.error("updateOldApplicableContestSeason:" + map.size());
        map.forEach((id, info) -> {
            Organization organization = info.getOrganization();
            ContestSeason contestSeason = info.getContestSeason();
            OrganizationApplicableContestSeason organizationApplicableContestSeason = organizationService.getOrInsertOrganizationApplicableContestSeason(organization, contestSeason);
            organizationApplicableContestSeason.setJoinState(1);
            organizationApplicableContestSeasonRepository.save(organizationApplicableContestSeason);
            logger.error("updateOldApplicableContestSeason:" + organization.getId() + ",:" + contestSeason.getId());
            result.add("updateOldApplicableContestSeason:" + organization.getId() + ",:" + contestSeason.getId());
        });
        logger.error("updateOldApplicableContestSeason:ok");
        return result;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "分页获取机构可报名的赛事")
    @RequestMapping(value = "api/organization/applicable/contest/season/page", method = RequestMethod.GET)
    Page<OrganizationApplicableContestSeason> applicableContestSeasonPage(@RequestParam("organizationId") String organizationId,
                                                                          @RequestParam int page,
                                                                          @RequestParam int size) throws FrogException {
        Organization organization = organizationRepository.findById(organizationId).orElseThrow(() -> new FrogException(INTERNAL_SERVER_ERROR, "机构不存在"));
        Page<OrganizationApplicableContestSeason> result = contestSeasonRepository.onlineOrApplied(organizationId, PageRequest.of(page, size, Sort.by("create_time").descending())
        ).map(contestSeason -> organizationService.getOrInsertOrganizationApplicableContestSeason(organization, contestSeason));
        Date now = contestScheduleRepository.cachedNow();
        result.getContent().stream().forEach(e -> {
            ContestSeason contestSeason = e.getContestSeason();
            e.setProperty("cs", contestSeason);
            boolean alreadyStart = Optional.ofNullable(contestSeason).map(ContestSeason::getStartTime).map(now::after).orElse(false);
            boolean alreadyEnd = Optional.ofNullable(contestSeason).map(ContestSeason::getEndTime).map(now::after).orElse(false);
            if (alreadyStart && !alreadyEnd) {
                contestSeason.setProperty("onlineStatusName", "已上线");
            } else if (alreadyEnd) {
                contestSeason.setProperty("onlineStatusName", "已下线");
            } else {
                contestSeason.setProperty("onlineStatusName", "未上线");
            }
            contestSeason.setProperty("now", now);
        });

        return result;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "机构报名赛事")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/applicable/contest/season", method = RequestMethod.POST)
    @Transactional
    void applicableContestSeasonJoin(@RequestParam("id") String id, @RequestParam("channelCode") String channelCode, @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        OrganizationApplicableContestSeason organizationApplicableContestSeason = organizationApplicableContestSeasonRepository.findById(id).orElseThrow(() -> new FrogException(INTERNAL_SERVER_ERROR, "机构不可报名该赛事：" + id));
        organizationApplicableContestSeason.setJoinState(1);
        organizationApplicableContestSeason.setChannelCode(channelCode);

        //修改机构用户渠道
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        channelRegisterService.changeUserRegisterChannel(loginUser, channelCode, organizationApplicableContestSeason.getContestSeason().getId(), organizationApplicableContestSeason.getOrganization().getId());
        organizationApplicableContestSeasonRepository.save(organizationApplicableContestSeason);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "获取机构的赛事")
    @RequestMapping(value = "api/app/organization/contest/season", method = RequestMethod.GET)
    List<Object> orgContestSeason(@RequestParam("organizationId") String organizationId) throws FrogException {
        return organizationRepository.findById(organizationId).map(this::getApplicableContestSeason).orElseThrow(() -> new FrogException(FORBIDDEN, ""));
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "获取机构特有作品")
    @RequestMapping(value = "api/organization/virtual/works", method = RequestMethod.GET)
    List<Works> virtualWorks(
            @RequestParam(value = "contestSeasonId", required = false, defaultValue = Const.defaultSeasonId) String contestSeasonId,
            @RequestParam("organizationId") String organizationId,
            @RequestParam("itemId") String itemId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) {

        Specification<Works> empty = Specification.where(null);

        Specification<Works> itemCondition = Optional.ofNullable(itemId).map(org.apache.commons.lang3.StringUtils::trim).filter(v -> !StringUtils.isEmpty(v))
                .map(v -> (Specification<Works>) (root, query, criteriaBuilder) ->
                        criteriaBuilder.equal(root.get("contestant").get("contestItem").get("id"), v)
                ).orElse(empty);
        Specification<Works> organizationCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestant").get("contestantInfo").get("organization").get("id"), organizationId);
        Specification<Works> contestantTypeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestant").get("contestantInfo").get("contestantType"), ContestantType.ORG_VIRTUAL);
        Specification<Works> deletedCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
        Specification<Works> contestSeasonCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestant").get("contestantInfo").get("contestSeason").get("id"), contestSeasonId);


        List<Works> worksList = worksRepository.findAll(Specification.where(itemCondition)
                        .and(organizationCondition).and(contestantTypeCondition).and(deletedCondition).and(contestSeasonCondition)
                , Sort.by("createTime").descending());


//        ContestItem item = contestItemRepository.findById(itemId).orElse(null);
//        if (item == null) {
//            worksList = worksRepository.findByContestant_ContestantInfo_Organization_IdAndContestant_ContestantInfo_ContestantTypeAndDeletedOrderByCreateTimeDesc(organizationId, ContestantType.ORG_VIRTUAL, false);
//        } else {
//            worksList = worksRepository.findByContestant_ContestItemAndContestant_ContestantInfo_Organization_IdAndContestant_ContestantInfo_ContestantTypeAndDeletedOrderByCreateTimeDesc(item, organizationId, ContestantType.ORG_VIRTUAL, false);
//
//        }
        worksList.stream().forEach(works -> setContestantItemInfo(works));
        return worksList;
    }

    private void setContestantItemInfo(Works works) {
        works.setProperty("contestItemName", works.getContestant().getContestItem().getName());
        works.setProperty("contestItemId", works.getContestant().getContestItem().getId());
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "删除机构特有作品")
    @RequestMapping(value = "api/organization/virtual/works", method = RequestMethod.DELETE)
    void deleteVirtualWorks(
            @RequestParam("worksId") String worksId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        Works works = worksRepository.findById(worksId).orElse(null);
        if (works != null) {
            worksService.delete(works);
        }
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "机构关注者列表")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/follower/page", method = RequestMethod.GET)
    Page<OrganizationFollower> followerUserList(
            @RequestParam("organizationId") String organizationId,
            @RequestParam int page,
            @RequestParam int size,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        Page<OrganizationFollower> result = organizationFollowerRepository.findAll(
                Specification.where((root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.equal(root.get("organizationId"), organizationId)),
                PageRequest.of(0, 100, Sort.by("createTime").descending()));
        result.forEach(organizationFollower -> organizationFollower.setProperty("loginUser", Optional.ofNullable(organizationFollower).map(OrganizationFollower::getLoginUserId)
                .flatMap(loginUserRepository::findById)
                .map(loginUser -> objectMapper.valueToTree(loginUser))
                .orElse(null)
        ));
        return result;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "关注机构")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/follow", method = RequestMethod.POST)
    void followOrganization(
            @RequestParam("organizationId") String organizationId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        organizationService.followOrganization(frogUserDetails.getLoginUserId(), organizationId);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "取消关注机构")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/unFollow", method = RequestMethod.POST)
    void unFollowOrganization(
            @RequestParam("organizationId") String organizationId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        organizationService.unFollowOrganization(frogUserDetails.getLoginUserId(), organizationId);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "上传机构特有作品")
    @RequestMapping(value = "api/organization/virtual/works/upload", method = RequestMethod.POST)
    Works saveVirtualWorks(
//            @RequestParam(value = "contestSeasonId", required = false, defaultValue = Const.defaultSeasonId) String contestSeasonId,
            @RequestParam("organizationId") String organizationId,
            @RequestParam("itemId") String itemId,
            @RequestParam("fileId") String fileId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {

        Organization organization = organizationRepository.findById(organizationId).orElseThrow(() -> new FrogException(INTERNAL_SERVER_ERROR, "机构不存在:" + organizationId));
        ContestItem contestItem = contestItemRepository.findById(itemId).orElseThrow(() -> new FrogException(INTERNAL_SERVER_ERROR, "赛项不存在:" + itemId));
        LoginUserFile loginUserFile = loginUserFileRepository.findById(fileId).orElseThrow(() -> new FrogException(INTERNAL_SERVER_ERROR, "文件不存在:" + fileId));
//        ContestSeason contestSeason = contestSeasonRepository.findById(contestSeasonId).orElseThrow(() -> new FrogException(INTERNAL_SERVER_ERROR, "赛季不存在:" + contestSeasonId));
        Works works = organizationService.saveVirtualWorks(organization, contestItem, loginUserFile);
        setContestantItemInfo(works);
        return works;
    }

    @Autowired
    OrganizationFollowerRepository organizationFollowerRepository;

    @RuntimeDoc(client = {RuntimeDoc.Client.Console, RuntimeDoc.Client.Api}, desc = "获取机构详细信息")
    @RequestMapping(value = "api/organization", method = RequestMethod.GET)
    Organization save(
            @RequestParam("id") String id,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        Organization organization = organizationRepository.findById(id).get();
        organization.setProperty("followed", Optional.ofNullable(frogUserDetails).map(FrogUserDetails::getLoginUserId)
                .map(loginUserId -> organizationFollowerRepository.countByOrganizationIdAndLoginUserId(id, loginUserId))
                .orElse(0l) > 0
        );
        organization.setProperty("followerCount", organizationFollowerRepository.countByOrganizationId(id));
        return organization;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "机构作品feed列表")
    @RequestMapping(value = "api/organization/works/by/contest/season", method = RequestMethod.GET)
    List<FeedItem> worksList(
            @RequestParam(value = "contestSeasonId", required = false, defaultValue = "") String contestSeasonId,
            @RequestParam("organizationId") String organizationId,
            @RequestParam(value = "ot", required = false, defaultValue = "-1") int ot,
            @RequestParam(value = "nt", required = false, defaultValue = "-1") int nt,
            @RequestParam(value = "limit", required = false) int limit,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        PageRequest page = pageService.getPage(ot, nt, limit, Sort.by("createTime").descending());

        Specification<Works> notDeleted = (root, query, criteriaBuilder) -> criteriaBuilder.not(root.get("deleted"));
        Specification<Works> contestSeason = StringUtils.isEmpty(contestSeasonId) ? Specification.where(null) : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestant").get("contestantInfo").get("contestSeason").get("id"), contestSeasonId);
        Page<Works> worksList = worksRepository.findAll(Specification.where(notDeleted)
                .and((root, query, criteriaBuilder) -> {
                    Path<Object> contestantInfo = root.get("contestant").get("contestantInfo");
                    return criteriaBuilder.and(
                            criteriaBuilder.not(contestantInfo.get("deleted")),
                            criteriaBuilder.equal(contestantInfo.get("organization").get("id"), organizationId)
                    );
                }).and(contestSeason), page
        );


        List<FeedItem> itemList = worksList.stream()
                .map(works -> worksService.toFeedItem(works)).collect(Collectors.toList());

        for (FeedItem item : itemList) {
            item.setProperty("score", page.getPageNumber() + 1);
        }
        return itemList;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "机构作品feed列表(老)", deprecated = "1.1.4")
    @RequestMapping(value = "api/organization/works/ranked", method = RequestMethod.GET)
    List<FeedItem> worksList(
            @RequestParam("organizationId") String organizationId,
            @RequestParam(value = "ot", required = false, defaultValue = "-1") int ot,
            @RequestParam(value = "nt", required = false, defaultValue = "-1") int nt,
            @RequestParam(value = "limit", required = false) int limit,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        PageRequest page = pageService.getPage(ot, nt, limit);

        List<FeedItem> itemList = worksRepository.findForOrganization(organizationId, page).stream()
                .map(works -> worksService.toFeedItem(works)).collect(Collectors.toList());

        for (FeedItem item : itemList) {
            item.setProperty("score", page.getPageNumber() + 1);
        }
        return itemList;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "保存机构")
    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN') ||hasAuthority('ADMIN_ORGANIZATIONS') )")
    @RequestMapping(value = "api/organization", method = RequestMethod.POST)
    Organization save(@AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestBody Organization organization) throws FrogException {
        Object logoFileId = organization.getProperties().get("logoFileId");
        if (organization.getLogo() == null && logoFileId == null) {
            throw new FrogException(FORBIDDEN, "机构logo必填");
        }
        return organizationService.save(organization);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "保存机构(包括管理员列表)")
    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN') ||hasAuthority('ADMIN_ORGANIZATIONS') )")
    @RequestMapping(value = "api/organization/all", method = RequestMethod.POST)
    Organization saveAll(@AuthenticationPrincipal FrogUserDetails frogUserDetails,
                         @RequestBodyPartResolverConfig.RequestBodyPart("organization") Organization organization,
                         @RequestBodyPartResolverConfig.RequestBodyPart("administrators") OrganizationService.OrganizationAdmin[] administrators
    ) throws FrogException {
        Object logoFileId = organization.getProperties().get("logoFileId");
        if (organization.getLogo() == null && logoFileId == null) {
            throw new FrogException(FORBIDDEN, "机构logo必填");
        }
        return organizationService.save(organization, administrators, frogUserDetails.getLoginUser(loginUserRepository));
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "获取指定机构的管理员列表")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/administrators", method = RequestMethod.GET)
    List<OrganizationService.OrganizationAdmin> administrators(@RequestParam String organizationId) {
        return organizationService.administrators(organizationId);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "获取我的机构(多个机构报名的话只返回一个)")
    @RequestMapping(value = "api/my/organization", method = RequestMethod.GET)
    Organization getMyOrganization(@AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        return Optional.ofNullable(frogUserDetails)
                .map(FrogUserDetails::getLoginUserId)
                .flatMap(loginUserId -> contestantInfoRepository.findByAgentLoginUser_IdAndContestantTypeAndDeleted(loginUserId, ContestantType.INDIVIDUAL, false)
                        .stream()
                        .map(contestantInfo -> contestantInfo.getOrganization())
                        .filter(organization -> organization != null)
                        .findFirst())
                .orElse(null);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "删除机构")
    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN') ||hasAuthority('ADMIN_ORGANIZATIONS') )")
    @RequestMapping(value = "api/organization", method = RequestMethod.DELETE)
    void delete(@AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestParam(value = "id") String id) {
        organizationService.deleteById(id);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "获取所有机构")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/all", method = RequestMethod.GET)
    Iterable<Organization> findAll() {
        return organizationRepository.findAll(Sort.by("name"));
    }


    @Autowired
    FeedItemRepository feedItemRepository;

    @RuntimeDoc(client = {RuntimeDoc.Client.Api}, desc = "获取机构分组")
    @RequestMapping(value = "api/organization/by/group", method = RequestMethod.GET)
    List<OrganizationGroup> organizationGroupList() {
        List<OrganizationGroup> groupList = new ArrayList<>();

        {
            Specification<FeedItem> empty = Specification.where(null);
            Specification<FeedItem> deletedCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
            Specification<FeedItem> feedIdCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("feedId"), "organizationRecommendedList");
            Specification<FeedItem> scoreCondition = empty;

            List<Organization> recommendedList = feedItemRepository.findAll(Specification
                            .where(deletedCondition)
                            .and(feedIdCondition)
                            .and(scoreCondition)
                    , Sort.by("score").descending()).stream()
                    .map(FeedItem::getEntity).map(entity -> organizationRepository.findById(entity).orElse(null)).filter(Objects::nonNull).collect(Collectors.toList());
            if (recommendedList.size() > 0) {
                groupList.add(new OrganizationGroup("recommendedList", recommendedList, objectMapper));
            }

        }


        Map<String, ArrayList<Organization>> map = new HashMap<>();
        Page<Organization> pageResult = organizationRepository.findByVirtualOrganization(false, PageRequest.of(0, 1000, Sort.by("name").descending().and(Sort.by("id").descending())));
        for (Organization organization : pageResult.getContent()) {
            String groupName = Optional.ofNullable(organization).map(Organization::getName).map(name -> name.toUpperCase()).map(name -> name.toCharArray()).filter(chars -> chars.length > 0).map(chars -> chars[0]).map(c -> {
                if (c >= 'A' && c <= 'Z') {
                    return new String(new char[]{c});
                }
                String[] pinyin = PinyinHelper.toHanyuPinyinStringArray(c);
                if (pinyin != null && pinyin.length > 0 && pinyin[0].length() > 0) {
                    return pinyin[0].toUpperCase().substring(0, 1);
                }
                return "#";
            }).orElse("#");
            map.computeIfAbsent(groupName, n -> new ArrayList<>()).add(organization);
        }
        ArrayList<Organization> others = map.remove("#");
        map.entrySet().stream().map(e -> new OrganizationGroup(e.getKey(), e.getValue(), objectMapper))
                .sorted(Comparator.comparing(OrganizationGroup::getName)).forEach(groupList::add);
        if (others != null && others.size() > 0) {
            groupList.add(new OrganizationGroup("#", others, objectMapper));
        }
        return groupList;
    }


    public static class OrganizationGroup {
        private final String name;
        private final List<Object> children;

        public OrganizationGroup(String name, List<Organization> children, ObjectMapper objectMapper) {
            this.name = name;
            this.children = children.stream().map(o -> objectMapper.valueToTree(o)).collect(Collectors.toList());
        }

        public String getName() {
            return name;
        }

        public List<Object> getChildren() {
            return children;
        }
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Api}, desc = "分页获取所有机构")
    @RequestMapping(value = "api/organization/page", method = RequestMethod.GET)
    Page<Organization> fetch(
            @RequestParam(required = false) String search,
            @RequestParam int page,
            @RequestParam int size,
            @RequestParam(value = "__client", required = false, defaultValue = "") String client,
            @RequestParam(value = "virtualOrganization", required = false) Boolean virtualOrganization
    ) {
        if (!"frog-api".equalsIgnoreCase(client)) {
            return fetch2(search, page, size);
        }
        Sort orders = Sort.by("createTime").descending();
        orders = Sort.by("priority").descending().and(orders);
        Page<Organization> pageResult = organizationRepository.findByVirtualOrganization(false, PageRequest.of(page, size, orders));
        pageResult.getContent().forEach(organization -> {
            organization.setProperty("letterGroup", Optional.ofNullable(organization).map(Organization::getName).map(name -> name.toUpperCase()).map(name -> name.toCharArray()).filter(chars -> chars.length > 0).map(chars -> chars[0]).map(c -> {
                if (c >= 'A' && c <= 'Z') {
                    return new String(new char[]{c});
                }
                String[] pinyin = PinyinHelper.toHanyuPinyinStringArray(c);
                if (pinyin != null && pinyin.length > 0 && pinyin[0].length() > 0) {
                    return pinyin[0].toUpperCase().substring(0, 1);
                }
                return "#";
            }).orElse("#"));
        });
        return pageResult;
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "分页获取所有机构")
    @RequestMapping(value = "api/organization/admin/page", method = RequestMethod.GET)
    Page<Organization> fetch2(
            @RequestParam(required = false) String search,
            @RequestParam int page,
            @RequestParam int size
    ) {
        PageRequest pageRequest = StringUtils.isEmpty(search) ? PageRequest.of(page, size, Sort.by("createTime").descending()) : PageRequest.of(page, size);
        Specification<Organization> empty = Specification.where(null);
        Specification<Organization> searchCondition = Optional.ofNullable(search).map(String::trim).map(v -> StringUtils.isEmpty(v) ? null : v).map(v -> (Specification<Organization>) (root, query, criteriaBuilder) ->
                criteriaBuilder.greaterThan(criteriaBuilder.function("match", Double.class, root.get("search"), new LiteralExpression<String>((CriteriaBuilderImpl) criteriaBuilder, injectSpace(v))), 0.)
        ).orElse(empty);
        Page<Organization> all = organizationRepository.findAll(Specification.where(searchCondition), pageRequest);
        for (Organization organization : all) {
            organization.setProperty("contestSeasonList", getApplicableContestSeason(organization)
            );
        }
        return all;
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "分页获取所有已审核机构")
    @RequestMapping(value = "api/organization/admin/page/audit", method = RequestMethod.GET)
    Page<Organization> fetch3(
            @RequestParam(required = false) String search,
            @RequestParam int page,
            @RequestParam int size
    ) {
        PageRequest pageRequest = StringUtils.isEmpty(search) ? PageRequest.of(page, size, Sort.by("createTime").descending()) : PageRequest.of(page, size);
        Specification<Organization> empty = Specification.where(null);
        Specification<Organization> searchCondition = Optional.ofNullable(search).map(String::trim).map(v -> StringUtils.isEmpty(v) ? null : v).map(v -> (Specification<Organization>) (root, query, criteriaBuilder) ->
                criteriaBuilder.greaterThan(criteriaBuilder.function("match", Double.class, root.get("search"), new LiteralExpression<String>((CriteriaBuilderImpl) criteriaBuilder, injectSpace(v))), 0.)
        ).orElse(empty);
        Specification<Organization> audit = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("auditStatus"), 1);
        Page<Organization> all = organizationRepository.findAll(Specification.where(searchCondition).and(audit), pageRequest);
        for (Organization organization : all) {
            organization.setProperty("contestSeasonList", getApplicableContestSeason(organization)
            );
        }
        return all;
    }

    @Autowired
    ContestScheduleRepository contestScheduleRepository;

    private List<Object> getApplicableContestSeason(Organization organization) {
        //2020-05-14 lizf: 要求机构报名不再限制可以报名哪些赛事,改为可以随便报名
        Specification<ContestSeason> timeCondition = (root, query, criteriaBuilder) ->
                criteriaBuilder.between(criteriaBuilder.function("now", Date.class), root.get("startTime"), root.get("endTime"));
        Specification<ContestSeason> notDeleted = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
        Specification<ContestSeason> contestSeasonTypeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestSeasonType"), ContestSeason.ContestSeasonType.CONTEST);

        return contestSeasonRepository.findAll(Specification
                .where(notDeleted)
                .and(timeCondition)
                .and(contestSeasonTypeCondition)
        ).stream()
                .map(contestSeason -> objectMapper.valueToTree(contestSeason))
                .distinct()
                .collect(Collectors.toList());
//        return organizationApplicableContestSeasonRepository.findAll(Specification
//                .where((Specification<OrganizationApplicableContestSeason>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), organization.getId())))
//                .stream().map(OrganizationApplicableContestSeason::getContestSeason)
//                .map(contestSeason -> {
//                    contestSeason.setProperty("now", contestScheduleRepository.cachedNow());
//                    return contestSeason;
//                })
//                .map(contestSeason -> objectMapper.valueToTree(contestSeason))
//                .distinct()
//                .collect(Collectors.toList());
    }

    private Page<OrganizationApplicableContestSeason> getApplicableContestSeason(Organization organization, int page, int size) {
        Page<OrganizationApplicableContestSeason> result = organizationApplicableContestSeasonRepository.findAll(Specification
                .where((Specification<OrganizationApplicableContestSeason>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), organization.getId())), PageRequest.of(page, size));

        Date now = contestScheduleRepository.cachedNow();
        result.getContent().stream().forEach(e -> {
            ContestSeason contestSeason = e.getContestSeason();
            e.setProperty("cs", contestSeason);
            boolean alreadyStart = Optional.ofNullable(contestSeason).map(ContestSeason::getStartTime).map(now::after).orElse(false);
            boolean alreadyEnd = Optional.ofNullable(contestSeason).map(ContestSeason::getEndTime).map(now::after).orElse(false);
            if (alreadyStart && !alreadyEnd) {
                contestSeason.setProperty("onlineStatusName", "已上线");
            } else if (alreadyEnd) {
                contestSeason.setProperty("onlineStatusName", "已下线");
            } else {
                contestSeason.setProperty("onlineStatusName", "未上线");
            }
        });

        return result;
    }

//    public static class OrganizationAdmin {
//        private String phone;
//        private boolean superAdmin;
//
//        public boolean isSuperAdmin() {
//            return superAdmin;
//        }
//
//        public void setSuperAdmin(boolean superAdmin) {
//            this.superAdmin = superAdmin;
//        }
//
//        public String getPhone() {
//            return phone;
//        }
//
//        public void setPhone(String phone) {
//            this.phone = phone;
//        }
//
//
//    }


    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "获取我可以管理的第一个机构")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping("api/app/my/admin/organization")
    @ResponseBody
    @Transactional(rollbackFor = Throwable.class)
    public Optional<Organization> adminOrganizations(@AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        Optional<Organization> organizationOptional = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .filter(a -> a.getAuthorityEnum() == Authority.ADMIN_ORGANIZATION || a.getAuthorityEnum() == Authority.SUPER_ADMIN_ORGANIZATION)
                .map(a -> {
                    Optional<Organization> optionalOrganization = organizationRepository.findById(a.getEntityId());
                    optionalOrganization.ifPresent(organization -> organization.setProperty("order", a.getAuthorityEnum() == Authority.SUPER_ADMIN_ORGANIZATION ? 0 : 1));
                    optionalOrganization.ifPresent(organization -> organization.setProperty("superAdmin", a.getAuthorityEnum() == Authority.SUPER_ADMIN_ORGANIZATION));
                    return optionalOrganization;
                })
                .filter(a -> a.isPresent())
                .map(a -> a.get())
                .sorted(Comparator.comparingInt(o -> (Integer) o.getProperties().get("order")))
                .findFirst();
        organizationOptional.ifPresent(organization -> {
            String organizationId = organization.getId();
            organization.setProperty("followerCount", organizationFollowerRepository.countByOrganizationId(organizationId));
            organization.setProperty("contestSeasonCount", organizationApplicableContestSeasonRepository.countByOrganization(organizationId));

            List<Object> superAdmin = loginUserAuthorityRepository.findByAuthorityAndEntityId(Authority.SUPER_ADMIN_ORGANIZATION, organizationId).stream().map(a -> a.getLoginUser())
                    .map(loginUser -> {
                        loginUser.setProperty("superAdmin", true);
                        return objectMapper.valueToTree(loginUser);
                    })
                    .collect(Collectors.toList());
            List<Object> normalAdmin = loginUserAuthorityRepository.findByAuthorityAndEntityId(Authority.ADMIN_ORGANIZATION, organizationId).stream().map(a -> a.getLoginUser())
                    .map(loginUser -> {
                        loginUser.setProperty("superAdmin", false);
                        return objectMapper.valueToTree(loginUser);
                    })
                    .collect(Collectors.toList());
            List<Object> adminList = new ArrayList<>();
            adminList.addAll(superAdmin);
            adminList.addAll(normalAdmin);
            organization.setProperty("administratorList", adminList);
            organization.setProperty("heat", Optional.ofNullable(organization.getHeat()).map(BigDecimal::longValue).orElse(0L));
        });
        return organizationOptional;
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "获取我可以管理的所有机构")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping("api/organization")
    @ResponseBody
    @Transactional(rollbackFor = Throwable.class)
    public List<Organization> organizations(@AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        return frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .filter(a -> a.getAuthorityEnum() == Authority.ADMIN_ORGANIZATION || a.getAuthorityEnum() == Authority.SUPER_ADMIN_ORGANIZATION)
                .map(a -> organizationRepository.findById(a.getEntityId()))
                .filter(a -> a.isPresent())
                .map(a -> a.get())
                .collect(Collectors.toList());
    }

    private void checkUserOrganization(FrogUserDetails frogUserDetails, String organizationId) throws FrogException {
        long count = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .filter(a -> a.getAuthorityEnum() == Authority.ADMIN_ORGANIZATION || a.getAuthorityEnum() == Authority.SUPER_ADMIN_ORGANIZATION)
                .map(a -> organizationId.equals(a.getEntityId()))
                .count();
        if (count == 0) {
            throw new FrogException(FORBIDDEN, "当前用户没有对此机构对操作权限");
        }

    }

    @Autowired
    ContestSeasonService contestSeasonService;

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "保存机构报名信息")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/contestant", method = RequestMethod.POST)
    @ResponseBody
    public ContestantInfo saveContestant(String organizationId,
                                         @RequestParam(value = "__client", required = false, defaultValue = "") String client,
                                         @AuthenticationPrincipal FrogUserDetails frogUserDetails,
                                         @RequestBodyPartResolverConfig.RequestBodyPart("contestantInfo") ContestantInfo contestantInfo,
                                         @RequestBodyPartResolverConfig.RequestBodyPart("contestantMembers") ContestantInfo[] contestantMembers,
                                         @RequestBodyPartResolverConfig.RequestBodyPart(("worksList")) Works[] worksList,
                                         @RequestBodyPartResolverConfig.RequestBodyPart(("loginUserFiles")) LoginUserFile[] loginUserFiles
    ) throws FrogException {
        try {
            if (contestantInfo.getContestantType() == ContestantType.GROUP || contestantInfo.getContestantType() == ContestantType.GROUP_MEMBER) {
                throw new FrogException(INTERNAL_SERVER_ERROR, "暂时不允许组合报名");
            }
            if (StringUtils.isEmpty(contestantInfo.getClient())) {
                contestantInfo.setClient(client);
            }
            contestService.checkAgeRange(contestantInfo);
            if (contestantInfo.getContestSeason() == null) {
                contestantInfo.setContestSeason(contestSeasonRepository.findById(defaultSeasonId).orElse(null));
            }
            contestSeasonService.ensureCanApply(contestantInfo.getContestSeason());
            if (contestantMembers == null) {
                contestantMembers = new ContestantInfo[0];
            }
            Organization organization = organizationRepository.findById(organizationId).get();
            Arrays.stream(contestantMembers).forEach(m -> {
                if (m.getContestSeason() == null) {
                    m.setContestSeason(contestSeasonRepository.findById(defaultSeasonId).orElse(null));
                }
                m.setContestantType(ContestantType.GROUP_MEMBER);
                if (m.getOrganization() == null) {
                    m.setOrganization(organization);
                }
            });
            checkUserOrganization(frogUserDetails, organizationId);
            contestantInfo = organizationService.organizationSaveContestant(organization, contestantInfo, contestantMembers, worksList, loginUserFiles);

            addContestantInfoProperty(contestantInfo);
            return contestantInfo;
        } catch (Exception e) {
            logger.info("apply exception", e);
            try {
                Map<String, Object> map = new HashMap<>();
                map.put("contestantInfo", contestantInfo);
                map.put("contestantMembers", contestantMembers);
                map.put("worksList", worksList);
                map.put("loginUserFiles", loginUserFiles);
                map.put("loginUserId", Optional.ofNullable(frogUserDetails).map(FrogUserDetails::getLoginUserId).orElse(""));
                map.put("e", ExceptionUtils.getFullStackTrace(e));
                warnService.warn("console apply", objectMapper.writeValueAsString(map));
            } catch (Exception e1) {
                logger.info("apply exception", e1);
            }
            throw e;
        }
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "删除机构报名信息")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/contestant/info", method = RequestMethod.DELETE)
    @ResponseBody
    public void deleteContestant(String id,
                                 @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        Date now = contestScheduleRepository.cachedNow();
        ContestantInfo contestantInfo = contestantInfoRepository.findById(id).get();
        contestSeasonService.ensureCanApply(contestantInfo.getContestSeason());
        checkUserOrganization(frogUserDetails, contestantInfo.getOrganization().getId());
        contestService.deleteContestantInfo(contestantInfo);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "获取指定机构的所有报名信息")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/contestantInfo", method = RequestMethod.GET)
    @ResponseBody
    public List<ContestantInfo> contestants(
            @RequestParam(value = "contestSeasonId", required = false) String contestSeasonId,
            String organizationId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        checkUserOrganization(frogUserDetails, organizationId);
        try {
            Specification<ContestantInfo> deletedCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
            Specification<ContestantInfo> organizationCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), organizationId);
            Specification<ContestantInfo> empty = Specification.where(null);
            Specification<ContestantInfo> contestSeasonCondition = StringUtils.isEmpty(contestSeasonId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestSeason").get("id"), contestSeasonId);
            Specification<ContestantInfo> parentCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("parent_id"), null);
            List<ContestantInfo> contestantInfoList = contestantInfoRepository.findAll(Specification.where(deletedCondition)
                            .and(organizationCondition)
                            .and(contestSeasonCondition)
                            .and(parentCondition)
                    , Sort.by("createTime").descending());
            for (ContestantInfo contestantInfo : contestantInfoList) {
                addContestantInfoProperty(contestantInfo);
            }
            logger.warn("exceptionaaaaaaaaaaaaabbd");
            return contestantInfoList.stream().collect(Collectors.toList());
        } catch (Exception e) {
            logger.warn("exceptionaaaaaaaaaaaaabbc", e);
        }
        List<ContestantInfo> contestantInfos = contestantInfoRepository.findByOrganization_IdAndDeletedOrderByCreateTimeDesc(organizationId, false)
                .stream()
                .filter(contestantInfo -> contestSeasonId.equals(Optional.ofNullable(contestantInfo).map(ContestantInfo::getContestSeason).map(ContestSeason::getId).orElse("")))
                .filter(contestantInfo -> contestantInfo.getOrganization() != null)
                .filter(contestantInfo -> organizationId.equals(contestantInfo.getOrganization().getId()))
                .collect(Collectors.toList());
        for (ContestantInfo contestantInfo : contestantInfos) {
            addContestantInfoProperty(contestantInfo);
        }
        return contestantInfos.stream().filter(contestantInfo -> contestantInfo.getParent() == null).collect(Collectors.toList());
    }

    private void addContestantInfoProperty(ContestantInfo contestantInfo) {
        contestantInfo.setProperty("contestantMembers", contestantInfoRepository.findByParentAndDeleted(contestantInfo, false));
        List<Contestant> contestants = contestantRepository.findByContestantInfoAndDeleted(contestantInfo, false);
        for (Contestant contestant : contestants) {
            contestant.setProperty("worksList", worksRepository.findByContestantAndDeleted(contestant, false));
        }
        contestantInfo.setProperty("contestants", contestants);
    }

    @RequestMapping(value = "api/organization/qrcode.jpg", method = RequestMethod.GET)
    @ResponseBody
    public void qrcode(
            @RequestParam(value = "contestSeasonId", required = false, defaultValue = Const.defaultSeasonId) String contestSeasonId,
            @RequestParam(value = "organizationId") String organizationId,
            ServletResponse response) throws FrogException, MalformedURLException {
        organizationService.qrCode(organizationId, contestSeasonId, response);
    }

    @RequestMapping(value = "api/organization/apply/url", method = RequestMethod.GET)
    @ResponseBody
    public URL applyUrl(
            @RequestParam(value = "contestSeasonId", required = false, defaultValue = Const.defaultSeasonId) String contestSeasonId,
            @RequestParam(value = "organizationId") String organizationId
    ) throws FrogException, MalformedURLException {
        return organizationService.applyUrl(organizationId, contestSeasonId);
    }

    @RequestMapping(value = "api/organization/notice/manager", method = RequestMethod.POST)
    @ResponseBody
    public void noticeManager(
            @RequestParam(value = "organizationId") String organizationId
    ) throws FrogException {
        Organization organization = organizationRepository.findById(organizationId).orElseThrow(() -> new FrogException(INTERNAL_SERVER_ERROR, "机构[ID:" + organizationId + "]不存在"));

        List<OrganizationService.OrganizationAdmin> organizationAdmins = organizationService.administrators(organizationId);
        organizationAdmins.stream().filter(organizationAdmin -> !StringUtils.isEmpty(organizationAdmin.getPhone())).forEach(organizationAdmin -> {
            HashMap<String, String> params = new HashMap<>();
            params.put("name", organization.getName());
            try {
                smsService.send(organizationAdmin.getPhone(), params, SMS_196619870);
            } catch (FrogException e) {
                e.printStackTrace();
            }
        });
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "微信小程序保存机构")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/minicode", method = RequestMethod.POST)
    Organization saveOrganization(@AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestBody Organization organization) throws FrogException {
       /* Object logoFileId = organization.getProperties().get("logoFileId");
        if (organization.getLogo() == null && logoFileId == null) {
            throw new FrogException(FORBIDDEN, "机构logo必填");
        }*/
        if (organizationService.findByName(organization.getName())) {
            throw new FrogException(500, "名称已存在");
        }
        organization.setAuditStatus(0);
        organization.setProperty("fromchannel", "minicode");
        organization.setOrganizationType(Organization.OrganizationType.society);
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        organization.setCreateLoginUser(loginUser);
        Organization organization1 = organizationService.save(organization);
        Object nickName = organization.getProperties().getOrDefault("nickName", "");
//        if(!StringUtils.isEmpty(nickName)){
//            loginUser.setName(nickName.toString());
//        }
        // 设置当前用户为主要机构管理员
        loginUserService.addSuperadminOrganization(loginUser, organization1.getId(), nickName.toString());
        return organization1;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "添加机构管理员")
    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN') || hasAuthority('ADMIN_ORGANIZATIONS') || hasAuthority('SUPER_ADMIN_ORGANIZATION'))")
    @RequestMapping(value = "api/organization/addadmin", method = RequestMethod.POST)
    void addAdminForOrganization(@AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestParam(name = "phone") String phone, @RequestParam(name = "orgId") String orgId, @RequestParam(value = "nickName", required = false) String nickName) {
        organizationService.addAdminForOrganization(phone, orgId, nickName);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "设置机构logo")
    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN') || hasAuthority('ADMIN_ORGANIZATION') || hasAuthority('SUPER_ADMIN_ORGANIZATION'))")
    @RequestMapping(value = "api/organization/logo/setting", method = RequestMethod.POST)
    Organization addLogForOrg(@AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestParam(name = "logoFileId") String logoFileId, @RequestParam(name = "orgId") String orgId) throws FrogException {
        return organizationService.settingLogoForOrg(orgId, logoFileId);
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "删除机构管理员")
    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN') || hasAuthority('SUPER_ADMIN_ORGANIZATION')  || hasAuthority('ADMIN_ORGANIZATIONS'))")
    @RequestMapping(value = "api/organization/remadmin", method = RequestMethod.DELETE)
    void deleteAdminForOrggnization(@AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestParam(name = "phone") String phone, @RequestParam(name = "orgId") String orgId) {
        organizationService.deleteAdminForOrggnization(phone, orgId);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "更换机构主要管理员")
    @PreAuthorize("isAuthenticated() && hasAuthority('SUPER_ADMIN_ORGANIZATION')")
    @RequestMapping(value = "api/organization/changeSuperAdmin", method = RequestMethod.POST)
    void changeSuperAdminForOrggnization(@AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestParam(name = "oldPhone") String oldPhone, @RequestParam(name = "newPhone") String newPhone, @RequestParam(name = "orgId") String orgId) {
        organizationService.changeSuperAdminForOrggnization(oldPhone, newPhone, orgId);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "获取待审核机构列表")
    @RequestMapping(value = "api/organization/audit/status", method = RequestMethod.GET)
    public List<Organization> checkAuditStatus(
    ) throws FrogException {
        List<Organization> list = new ArrayList();
        list = organizationRepository.checkAuditStatus();
        for (Organization organization : list) {
            if (organization.getCreateLoginUser() != null) {
                organization.setProperty("loginUserPhone", organization.getCreateLoginUser().getPhone());
                organization.setProperty("loginUserName", organization.getCreateLoginUser().getName());
            }
        }
        return list;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "机构审核")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN') || hasAuthority('ADMIN_ORGANIZATIONS'))")
    @RequestMapping(value = "api/organization/audit/org", method = RequestMethod.POST)
    public void saveAuditOrg(
            @RequestParam(value = "id") String id,
            @RequestParam(value = "ifTrue") int ifTrue
    ) throws FrogException {
        Organization organization = organizationRepository.findById(id).orElse(null);
        if (organization == null) {
            throw new FrogException(500, "机构不存在");
        }
        organizationService.saveAuditOrg(organization, ifTrue);
    }

    /**
     * 生成报名海报图片
     *
     * @param contestSeasonId
     * @param organizationId
     * @param response
     * @throws FrogException
     * @throws MalformedURLException
     */
    @RequestMapping(value = "api/organization/post.jpg", method = RequestMethod.GET)
    @PreAuthorize("isAuthenticated()")
    @ResponseBody
    public void signUppost(
            @RequestParam(value = "contestSeasonId", required = false, defaultValue = Const.defaultSeasonId) String contestSeasonId,
            @RequestParam(value = "organizationId") String organizationId,
            ServletResponse response) throws FrogException, MalformedURLException {
        organizationService.signUpPost(organizationId, contestSeasonId, response);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "检查登录人是否是机构超级管理员")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/organization/checkSuperAdmin", method = RequestMethod.GET)
    Boolean checkSuperAdmin(@AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestParam(value = "organizationId") String organizationId) {
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        List<LoginUserAuthority> loginUserAuthority = loginUserAuthorityRepository.findByLoginUser_IdOrderByAuthorityAsc(loginUser.getId());
        for (LoginUserAuthority l : loginUserAuthority) {
            if (l.getAuthority().equals(Authority.SUPER_ADMIN_ORGANIZATION) && l.getEntityId().equals(organizationId)) {
                return true;
            }
        }
        return false;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "查询全部团队成员")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/fitness/team/members", method = RequestMethod.GET)
    List<Map<String, String>> getMembers(
            @RequestParam(value = "organizationId") String organizationId){
        List<Map<String, String>> Members = loginUserAuthorityRepository.getMembers(organizationId);
        return Members;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "团队成员列表")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/fitness/team/members/page", method = RequestMethod.GET)
    List<LoginUser> teamMembers(
            @RequestParam(value = "organizationId") String organizationId,
            @RequestParam(value = "onlyCoach", required = false, defaultValue = "0") int onlyCoach,
            @RequestParam int page,
            @RequestParam int size) {
        List<LoginUser> listResult = new ArrayList<>();
        Page<String> userIds = loginUserAuthorityRepository.getMemberIds(organizationId, PageRequest.of(page, size));
        for (String id : userIds) {
            LoginUser loginUser = loginUserRepository.findById(id).orElse(null);
            if (null != loginUser) {
                List<String> authList = loginUserAuthorityRepository.getAuthList(organizationId, loginUser.getId());
                Boolean isCoach = false;
                Boolean isAdmin = false;
                for (String auth : authList) {
                    if ("COACH".equals(auth)) {
                        isCoach = true;
                    } else if ("ADMIN_ORGANIZATION".equals(auth)) {
                        isAdmin = true;
                    }
                }
                loginUser.setProperty("isCoach", isCoach);
                loginUser.setProperty("isAdmin", isAdmin);
                //只查教练或查全部
                if (0 == onlyCoach || (0 != onlyCoach && !isAdmin)) {
                    //写入入驻时间
                    List<Date> joinTime = loginUserAuthorityRepository.getCreateTime(organizationId, loginUser.getId());
                    if (joinTime.size() > 0) {
                        loginUser.setProperty("joinTime", joinTime.get(0));
                    }
                    //写入机构内nickname
                    List<String> nickNameList = loginUserAuthorityRepository.getNickName(organizationId,loginUser.getId());
                    if (nickNameList.size() > 0) {
                        loginUser.setProperty("nickName", nickNameList.get(0));
                    }
                    List<String> avatarList = loginUserFileRepository.getAvatarId(loginUser.getId());
                    if(avatarList.size() > 0){
                        loginUser.setProperty("avatarId",avatarList.get(0));
                    }
                    listResult.add(loginUser);
                }
            }
        }
        return listResult;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "新增团队成员")
    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATION')")
    @RequestMapping(value = "api/fitness/add/member", method = RequestMethod.POST)
    void addMember(@AuthenticationPrincipal FrogUserDetails frogUserDetails,
                   @RequestParam(name = "phone") String phone,
                   @RequestParam(name = "orgId") String orgId,
                   @RequestParam(value = "name") String name,
                   @RequestParam(value = "intro", required = false , defaultValue = "") String intro,
                   @RequestParam(value = "auth") String auth,
                   @RequestParam(value = "avatarId" ,required = false,defaultValue = "")String avatarId ) throws FrogException {
        organizationService.addMember(phone, orgId, name, intro, auth,avatarId);
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "修改团队成员")
    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATION')")
    @RequestMapping(value = "api/fitness/update/member", method = RequestMethod.POST)
    void updateMember(@AuthenticationPrincipal FrogUserDetails frogUserDetails,
                   @RequestParam(name = "id") String id,
                   @RequestParam(name = "orgId") String orgId,
                   @RequestParam(value = "name",required = false) String name,
                   @RequestParam(value = "introduction", required = false , defaultValue = "") String introduction,
                   @RequestParam(value = "updateAuthority") String updateAuthority,
                   @RequestParam(value = "avatarId" ,required = false,defaultValue = "")String avatarId ) throws FrogException {
        boolean isAdmin = false;
        boolean isCoach = false;
        if("ADMIN_ORGANIZATION".equals(updateAuthority)){
            isAdmin = true;
        }
        if("COACH".equals(updateAuthority)){
            isCoach = true;
        }
        if(!isAdmin && !isCoach){
            organizationService.deleteMember(id, orgId);
        }else{
            if(StringUtils.isEmpty(name)){
                throw new FrogException(INTERNAL_SERVER_ERROR,"name is required");
            }
            organizationService.updateMember(id, orgId, name, introduction, isAdmin,isCoach,avatarId);
        }

    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "根据id修改机构名称")
    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATION')")
    @RequestMapping(value = "api/fitness/update/org/name", method = RequestMethod.POST)
    void updateOrgName(@AuthenticationPrincipal FrogUserDetails frogUserDetails,
                      @RequestParam(name = "orgId") String orgId,
                      @RequestParam(value = "newOrgName") String newOrgName) throws FrogException {
        organizationService.updateOrgName(orgId,newOrgName);
    }

   /* @Autowired
    StringRedisTemplate stringRedisTemplate;

    @GetMapping("api/st/{sign}")
    public void getUrl(HttpServletResponse response, @PathVariable String sign) throws IOException {
        BoundValueOperations<String, String> ops = stringRedisTemplate.boundValueOps(sign);
        String toUrl = ops.get();
        if (toUrl != null) {
            // 重定向
            response.sendRedirect(toUrl);
        } else {
            // 处理不存在的情况

        }
    }

    @GetMapping("api/build/st")
    public void buildUrl() throws IOException {
        String url = "https://frog-api.shuyiwa.com/html/formScreen.html?organizationId=40288a8c786433f70178685e9d7003b7&contestSeasonId=40288a8d7868cf5801787d3862670f40";
        String key = ShortUrlGenerator.getShortUrl(url);
        stringRedisTemplate.opsForValue().set(key,url);
        System.out.println(key+" "+url);

    }*/

}

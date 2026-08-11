package com.shuyiwa.fitness.backend.service;

import com.google.zxing.WriterException;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.ContestantType;
import com.shuyiwa.fitness.backend.event.WorksAuditSuccessEvent;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.SecService;
import com.shuyiwa.fitness.backend.util.ImageUtils;
import com.shuyiwa.fitness.backend.util.QRCodeUtil;
import org.apache.commons.io.FileUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.event.EventListener;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import javax.imageio.ImageIO;
import javax.persistence.EntityManager;
import javax.servlet.ServletResponse;
import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.math.BigDecimal;
import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;
import java.util.*;
import java.util.function.Consumer;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import static com.shuyiwa.fitness.backend.domain.Sms.Template.SMS_216837191;
import static com.shuyiwa.fitness.backend.global.FrogException.FORBIDDEN;
import static com.shuyiwa.fitness.backend.global.FrogException.INTERNAL_SERVER_ERROR;

@Service
public class OrganizationService {
    private static final Log logger = LogFactory.getLog(OrganizationService.class);
    @Value("${com.shuyiwa.fitness.backend.works-dir:works-dir}")
    String wordsDir;
    @Value("${com.shuyiwa.fitness.backend.organization.register.url:https://frog-api.shuyiwa.com/html/formScreen.html}")
    String registerUrl;
    @Value("${com.shuyiwa.fitness.backend.tmpfile:tmpfile}")
    String tmpfile;
    @Value("${com.shuyiwa.fitness.backend.logoUrl:https://img.fitooss.com/fitness/coach.png}")
    String logoUrl;
    @Autowired
    OrganizationRepository organizationRepository;

    @Autowired
    ContestantRepository contestantRepository;

    @Autowired
    SecService secService;


    static String defaultcontBgUrl = "https://img.shuyiwa.com/contest/season/postbackground/default.png";

    //    @Autowired
//    ContestantMemberRepository contestantMemberRepository;

    //    @Autowired
//    DivisionContestantRepository divisionContestantRepository;
    @Autowired
    WorksRepository worksRepository;
    @Autowired
    LoginUserFileRepository loginUserFileRepository;
//    @Autowired
//    ContestDivisionRepository contestDivisionRepository;

    @Autowired
    LoginUserFileService loginUserFileService;

    @Autowired
    LoginUserRepository loginUserRepository;

    @Autowired
    NewsRepository newsRepository;

    @Autowired
    AppointmentRepository appointmentRepository;

    @Autowired
    EntityManager entityManager;
    @Autowired
    WorksService worksService;
    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;
    @Autowired
    LoginUserService loginUserService;
    @Autowired
    FeedItemService feedItemService;
    @Autowired
    SmsService smsService;

//    public Contestant signWithWorks(String contestDivisionId, ContestantType contestantType, String organizationId, String loginUserFileId, LoginUser loginUser) throws FrogException {
//
//        Contestant contestant = new Contestant();
//        contestant.setContestantType(contestantType);
//        contestant.setOrganization(organizationRepository.findById(organizationId).get());
//        contestantRepository.save(contestant);
//        Contestant signInfo = new Contestant();
//        signInfo.setParent(contestant);
//        contestantRepository.save(signInfo);
//        DivisionContestant divisionContestant = new DivisionContestant();
//        divisionContestant.setContestDivision(contestDivisionRepository.findById(contestDivisionId).get());
//        divisionContestant.setContestant(contestant);
//        divisionContestantRepository.save(divisionContestant);
//
//        Optional<LoginUserFile> optionalLoginUserFile = loginUserFileRepository.findById(loginUserFileId);
//        if (optionalLoginUserFile.isPresent()) {
//            LoginUserFile loginUserFile = optionalLoginUserFile.get();
//            Works works = new Works();
//            works.setDivisionContestant(divisionContestant);
//            String name = loginUserFile.getName();
//            if (name.startsWith("组队")) {
//                name = name.substring("组队".length());
//            }
//            int index = name.lastIndexOf(".");
//            if (index > 0) {
//                name = name.substring(0, index);
//            }
//            works.setName(name);
//            works.setContestant(contestant);
//            works.setFormat(Works.WorksFormat.fromUploadType(loginUserFile.getContentType()));
//            entityManager.flush();
//            entityManager.refresh(works);
//            works.setScore(works.getCreateTime().getTime());
//            worksService.save(works);
//            loginUserFileService.use(loginUserFile, works);
//        }
//        return contestant;
//    }

    @Autowired
    ContestItemRepository contestItemRepository;
    @Autowired
    ContestService contestService;
    @Autowired
    ContestantInfoRepository contestantInfoRepository;
    @Autowired
    ContestSeasonRepository contestSeasonRepository;
    @Autowired
    OrganizationApplicableContestSeasonRepository organizationApplicableContestSeasonRepository;
    @Autowired
    ContestScheduleRepository contestScheduleRepository;
    @Autowired
    OrganizationDataDayRepository organizationDataDayRepository;
    @Autowired
    WarnService warnService;


    public void updateHeat(Consumer<List<Organization>> consumer) {
        Date now = contestScheduleRepository.cachedNow();
        //未通过审核不计算热度
        Specification<Organization> audit = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("auditStatus"), "1");
        Specification<Organization> updateTime = (root, query, criteriaBuilder) -> criteriaBuilder.lessThan(root.get("nextUpdateHeatTime"), criteriaBuilder.function("now", Date.class));

        List<Organization> organizationList = organizationRepository.findAll(Specification.where(updateTime).and(audit)
                , PageRequest.of(0, 10)).getContent().stream().map(organization -> {
            long userCount = contestantInfoRepository.countDistinctUsersByOrganization(organization.getId()).longValue();
            long voteCount = worksRepository.sumVoteOfOrganization(organization.getId()).longValue();
            long likeCount = worksRepository.sumLikeOfOrganization(organization.getId()).longValue();
            long seasonCount = organizationApplicableContestSeasonRepository.countByOrganization(organization.getId()).longValue();
            long heat = likeCount + voteCount * 1000 + userCount * 10000 + seasonCount * 100000;
            organization.setHeat(new BigDecimal(heat).divide(new BigDecimal(10000)));
            organization.setNextUpdateHeatTime(new Date(now.getTime() + Duration.ofHours(24).toMillis()));
            organization.setProperty("voteCount", voteCount);
            organization.setProperty("likeCount", likeCount);
            organization.setProperty("userCount", userCount);
            return organization;
        }).collect(Collectors.toList());
        if (organizationList.size() > 0) {
            consumer.accept(organizationList);
        }
    }

    @Transactional
    public void saveUpdateHeat(List<Organization> organizationList) {
        organizationRepository.saveAll(organizationList);
        for (Organization organization : organizationList) {
            organizationDataDayRepository.saveOrganizationDataDay(organization.getId(), OrganizationDataDay.DataType.heat.name(), organization.getHeat());
            Long voteCount = (Long) organization.getProperties().getOrDefault("voteCount", 0l);
            organizationDataDayRepository.saveOrganizationDataDay(organization.getId(), OrganizationDataDay.DataType.vote.name(), new BigDecimal(voteCount));
            Long likeCount = (Long) organization.getProperties().getOrDefault("likeCount", 0l);
            organizationDataDayRepository.saveOrganizationDataDay(organization.getId(), OrganizationDataDay.DataType.like.name(), new BigDecimal(likeCount));
            Long userCount = (Long) organization.getProperties().getOrDefault("userCount", 0l);
            organizationDataDayRepository.saveOrganizationDataDay(organization.getId(), OrganizationDataDay.DataType.user.name(), new BigDecimal(userCount));
        }
    }


    @Transactional(rollbackFor = Throwable.class)
    public ContestantInfo organizationSaveContestant(Organization organization, ContestantInfo contestantInfo, ContestantInfo[] contestantMembers, Works[] worksList, LoginUserFile[] loginUserFiles) throws FrogException {
        if (worksList.length != loginUserFiles.length) {
            throw new FrogException(INTERNAL_SERVER_ERROR, "内部错误", "length of worksList should equal with length of loginUserFiles");
        }
        if (StringUtils.isEmpty(contestantInfo.getAgentPhone()) || !Pattern.matches("\\d{11}", contestantInfo.getAgentPhone())) {
            throw new FrogException(FORBIDDEN, "监护人手机号必填,且必须为11位数字");
        }
        {//关联监护人用户
            ContestantInfo finalContestantInfo = contestantInfo;
            LoginUser agentLoginUser = loginUserRepository.findByPhone(contestantInfo.getAgentPhone()).orElseGet(() -> {
                LoginUser loginUser = loginUserService.createLoginUser(finalContestantInfo.getAgentPhone(), null);
                return loginUser;
            });
            contestantInfo.setAgentLoginUser(agentLoginUser);
        }
        Object itemId = contestantInfo.getProperties().get("itemId");
        if (contestantInfo.getId() == null) {//新建
            contestantInfo.setOrganization(organization);
            contestantInfo = contestService.saveContestantInfo(contestantInfo);
            contestService.getOrCreateContestant(contestantInfo, itemId);
            entityManager.flush();
            entityManager.refresh(contestantInfo);
            if (contestantInfo.getContestantType() == ContestantType.GROUP) {
                for (ContestantInfo contestantMember : contestantMembers) {
                    String agentPhone = contestantMember.getAgentPhone();
                    LoginUser agentLoginUser = loginUserRepository.findByPhone(agentPhone).orElseGet(() -> {
                        LoginUser loginUser = loginUserService.createLoginUser(agentPhone, null);
                        return loginUser;
                    });
                    contestantMember.setAgentLoginUser(agentLoginUser);
                    contestantMember.setParent(contestantInfo);
                    contestantMember.setOrganization(organization);
                    contestService.saveContestantInfo(contestantMember);
                }
            }

            for (int i = 0; i < worksList.length; i++) {
                Works works = worksList[i];
                contestService.saveWorksContestant(contestantInfo, works, new HashMap<>());
                if (contestantInfo != null) {
                    works.setLoginUser(contestantInfo.getAgentLoginUser());
                }
                LoginUserFile loginUserFile = loginUserFileRepository.findById(loginUserFiles[i].getId()).get();
                if (loginUserFile != null) {
                    works = loginUserFileService.newUse(loginUserFile, works);
                }
                works = worksService.save(works);
            }
        } else {//修改
            //首先，只能修改自己组织下的选手
            ContestantInfo contestantInfoInDb = contestantInfoRepository.findById(contestantInfo.getId()).get();
            if (!contestantInfoInDb.getOrganization().getId().equals(organization.getId())) {
                throw new FrogException(FORBIDDEN, "只能修改自己组织下的选手");
            }
            ContestItem contestItem = contestService.getContestItem(itemId);
            contestantRepository.findByContestantInfoAndDeleted(contestantInfo, false).stream().forEach(contestant -> {
                //修改为指定的item
                contestant.setContestItem(contestItem);
                contestantRepository.save(contestant);
            });
            BeanUtils.copyProperties(contestantInfo, contestantInfoInDb, "contestantList");
            contestService.saveContestantInfo(contestantInfoInDb);

            if (contestantInfo.getContestantType() == ContestantType.INDIVIDUAL) {
                //删除原有的memberInfo
                contestantInfoRepository.findByParentAndDeleted(contestantInfo, false).forEach(contestantMember -> {
                    contestantMember.setDeleted(true);
                    contestService.saveContestantInfo(contestantMember);
                });
//                contestantMemberRepository.deleteByContestant(contestant);
            }
            Map<String, ContestantInfo> memberInDbMap = contestantInfoRepository.findByParentAndDeleted(contestantInfo, false).stream().collect(Collectors.toMap(e -> e.getId(), e -> e));
            if (contestantInfo.getContestantType() == ContestantType.GROUP) {
                for (ContestantInfo contestantMember : contestantMembers) {
                    String agentPhone = contestantMember.getAgentPhone();
                    LoginUser agentLoginUser = loginUserRepository.findByPhone(agentPhone).orElseGet(() -> {
                        LoginUser loginUser = loginUserService.createLoginUser(agentPhone, null);
                        return loginUser;
                    });
                    contestantMember.setAgentLoginUser(agentLoginUser);
                    contestantMember.setParent(contestantInfo);
                    if (contestantMember.getId() == null) {
                        contestantMember.setParent(contestantInfo);
                        contestService.saveContestantInfo(contestantMember);
                    } else {
                        ContestantInfo contestantMemberInDb = memberInDbMap.remove(contestantMember.getId());
                        if (contestantMemberInDb == null) {
                            logger.warn("want to update contestantMemberInDb by cannot find it:" + contestantMember.getId());
                        } else {
                            BeanUtils.copyProperties(contestantMember, contestantMemberInDb);
                            contestService.saveContestantInfo(contestantMemberInDb);
                        }
                    }
                }
                ;
            }
            //删除已删除的
            memberInDbMap.values().stream().forEach(member -> {
                        member.setDeleted(true);
                        contestService.saveContestantInfo(member);
                    }
            );


            Map<String, Works> worksInDbMap = worksRepository.findByContestant_ContestantInfoAndDeleted(contestantInfo, false).stream().collect(Collectors.toMap(e -> e.getId(), e -> e));
            for (int i = 0; i < worksList.length; i++) {
                Works works = worksList[i];
                contestService.saveWorksContestant(contestantInfo, works, new HashMap<>());
                if (contestantInfo != null) {
                    works.setLoginUser(contestantInfo.getAgentLoginUser());
                }
                if (works.getId() == null) {
                    LoginUserFile loginUserFile = loginUserFileRepository.findById(loginUserFiles[i].getId()).get();
                    if (loginUserFile != null) {
                        works = loginUserFileService.newUse(loginUserFile, works);
                    }
                    works = worksService.save(works);
                } else {
                    Works worksInDb = worksInDbMap.remove(works.getId());
                    if (worksInDb == null) {
                        logger.warn("want to update worksInDb by cannot find it:" + works.getId());
                    } else {
                        BeanUtils.copyProperties(works, worksInDb);
                        LoginUserFile loginUserFile = loginUserFiles[i];
                        if (loginUserFile != null) {
                            works = loginUserFileService.newUse(loginUserFile, works);
                        }
                        works = worksService.save(worksInDb);
                    }
                }
            }
            worksInDbMap.values().forEach(worksToBeDelete -> {
                worksService.delete(worksToBeDelete);
            });

        }
        return contestantInfo;
    }

    @Transactional(rollbackFor = Throwable.class)
    public OrganizationApplicableContestSeason getOrInsertOrganizationApplicableContestSeason(Organization organization, ContestSeason contestSeason) {
        Specification<OrganizationApplicableContestSeason> organizationCondition = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization"), organization);
        Specification<OrganizationApplicableContestSeason> contestSeasonCondition = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestSeason"), contestSeason);
        return organizationApplicableContestSeasonRepository.findOne(Specification
                .where(organizationCondition)
                .and(contestSeasonCondition)
        ).orElseGet(() -> {
            OrganizationApplicableContestSeason organizationApplicableContestSeason = new OrganizationApplicableContestSeason();
            organizationApplicableContestSeason.setOrganization(organization);
            organizationApplicableContestSeason.setContestSeason(contestSeason);
            return organizationApplicableContestSeasonRepository.save(organizationApplicableContestSeason);
        });
    }

    @Transactional(rollbackFor = Throwable.class)
    public void deleteById(String id) {
        feedItemService.deleteItemByEntity(FeedItem.EntityType.ORGANIZATION, id);
        Specification<OrganizationApplicableContestSeason> orgnizationCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), id);

        try {
            List<OrganizationApplicableContestSeason> collect = organizationApplicableContestSeasonRepository.findAll(Specification.where(orgnizationCondition)).stream().collect(Collectors.toList());
            collect.forEach(organizationApplicableContestSeasonRepository::delete);
        } catch (Exception e) {
            warnService.warn("delete OrganizationApplicableContestSeason exception", e);
        }
        organizationRepository.deleteById(id);
    }

    @Transactional(rollbackFor = Throwable.class)
    public Organization save(Organization organization) {
        organization = saveOrganization(organization);
        entityManager.flush();
        entityManager.refresh(organization);
        return organization;
    }

    public boolean findByName(String name) {
        int num = organizationRepository.countByName(name);
        if (num > 0) return true;
        return false;
    }

    @Transactional(rollbackFor = Throwable.class)
    public void fillOrganizationSearch() {
        organizationRepository.findBySearch(null, PageRequest.of(0, 10)).forEach(this::save);
    }

    private Organization saveOrganization(Organization organization) {
        organization.resetSearch();
        Object fromchannel = organization.getProperties().get("fromchannel");
        Organization org = organizationRepository.save(organization);
        if (fromchannel != null && !"minicode".equals(fromchannel.toString())) {
            contestantInfoRepository.findByOrganization_IdAndDeleted(org.getId(), false).forEach(contestantInfo -> {
                contestantRepository.findByContestantInfoAndDeleted(contestantInfo, false).forEach(contestant -> {
                    worksRepository.findByContestantAndDeleted(contestant, false).forEach(works -> {
                        works.resetAppSearch();
                        worksService.save(works);
                    });
                });
            });
        }
        return org;
    }

    @Transactional(rollbackFor = Throwable.class)
    public Organization save(Organization organization, OrganizationAdmin[] administrators, LoginUser loginUser) throws FrogException {
//        Set<String> contestSeasonList = Arrays.stream((organization.getProperties().getOrDefault("contestSeasonList", "") + "").split(","))
//                .filter(v -> !StringUtils.isEmpty(v)).collect(Collectors.toSet());
        organization = saveOrganization(organization);
        String organizationId = organization.getId();
        Object logoFileId = organization.getProperties().get("logoFileId");
        if (logoFileId != null) {
            LoginUserFile loginUserFile = loginUserFileRepository.findById(logoFileId.toString()).get();
            if (loginUserFile != null) {
                loginUserFileService.use(loginUserFile, organization);
            }
        }
        if (!StringUtils.isEmpty(organization.getLogo())) {
            LoginUserFile loginUserFile = loginUserFileService.getFromDiskUrl(organization.getLogo());
            if (loginUserFile != null) {
                organization.setLogo(loginUserFileService.upload("organization-logo", loginUserFile));
            }
        }
        entityManager.flush();
        entityManager.refresh(organization);
        { //处理管理员
            Map<String, LoginUserAuthority> adminMap = loginUserAuthorityRepository.findByAuthorityAndEntityId(Authority.ADMIN_ORGANIZATION, organization.getId()).stream().collect(Collectors.toMap(a -> a.getLoginUser().getPhone(), a -> a));
            Map<String, LoginUserAuthority> superAdminMap = loginUserAuthorityRepository.findByAuthorityAndEntityId(Authority.SUPER_ADMIN_ORGANIZATION, organization.getId()).stream().collect(Collectors.toMap(a -> a.getLoginUser().getPhone(), a -> a));
            for (OrganizationAdmin admin : administrators) {
                String phone = admin.getPhone();
                if (adminMap.containsKey(phone) && !admin.isSuperAdmin()) {
                    //原有，不变
                    adminMap.get(phone).setInEntityNickname(admin.getNickName());
                    adminMap.remove(phone);
                } else if (superAdminMap.containsKey(phone) && admin.isSuperAdmin()) {
                    //原有，不变
                    superAdminMap.get(phone).setInEntityNickname(admin.getNickName());
                    superAdminMap.remove(phone);
                } else {
                    //不存在，新增
                    LoginUser optionalLoginUser = loginUserRepository.findByPhone(phone).orElse(null);
                    if (optionalLoginUser == null) {
                        optionalLoginUser = loginUserService.getOrCreateLoginUser(phone, true, null);
                    }
                    LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
                    loginUserAuthority.setAuthority(admin.isSuperAdmin() ? Authority.SUPER_ADMIN_ORGANIZATION : Authority.ADMIN_ORGANIZATION);
                    loginUserAuthority.setEntityId(organization.getId());
                    loginUserAuthority.setLoginUser(optionalLoginUser);
                    loginUserAuthority.setInEntityNickname(admin.getNickName());
                    loginUserAuthorityRepository.save(loginUserAuthority);
                }
            }
            //删除之前有现在没有的
            adminMap.values().forEach(loginUserAuthority -> loginUserAuthorityRepository.delete(loginUserAuthority));
            superAdminMap.values().forEach(loginUserAuthority -> loginUserAuthorityRepository.delete(loginUserAuthority));
        }
//        {//处理赛季
//            List<OrganizationApplicableContestSeason> oldSeasonList = organizationApplicableContestSeasonRepository.findAll(Specification.where((Specification<OrganizationApplicableContestSeason>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), organizationId)));
//            //删除需要删除的
//            List<OrganizationApplicableContestSeason> trash = new ArrayList<>();
//            for (OrganizationApplicableContestSeason old : oldSeasonList) {
//                if (!contestSeasonList.remove(old.getContestSeason().getId())) {
//                    trash.add(old);
//                }
//            }
//            trash.stream().forEach(organizationApplicableContestSeasonRepository::delete);
//            //增加需要新增的
//            for (String contestSeasonId : contestSeasonList) {
//                Organization finalOrganization = organization;
//                contestSeasonRepository.findById(contestSeasonId).ifPresent(contestSeason -> {
//                    OrganizationApplicableContestSeason organizationApplicableContestSeason = new OrganizationApplicableContestSeason();
//                    organizationApplicableContestSeason.setOrganization(finalOrganization);
//                    organizationApplicableContestSeason.setContestSeason(contestSeason);
//                    organizationApplicableContestSeasonRepository.save(organizationApplicableContestSeason);
//                });
//            }
//        }
        return organization;
    }

    @Transactional
    public void saveAuditOrg(Organization organization, int ifTrue) throws FrogException {
        if (ifTrue == 1) {
            //修改机构审核状态
            organization.setAuditStatus(1);
            organizationRepository.save(organization);
            //查询管理员发送短信
            String phone = loginUserAuthorityRepository.getAdminPhone(organization.getId());
            sendOrgAuditCode(phone, organization.getName());
        } else if (ifTrue == 0) {//审核失败删除
            organizationApplicableContestSeasonRepository.deleteByOrganizationId(organization.getId());
            loginUserAuthorityRepository.deleteByEntityId(organization.getId());
            organizationRepository.deleteById(organization.getId());
        }
    }

    @Transactional
    public Works saveVirtualWorks(Organization organization, ContestItem contestItem, LoginUserFile loginUserFile) throws FrogException {
        Specification<ContestantInfo> deletedCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
        Specification<ContestantInfo> organizationCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization"), organization);
        Specification<ContestantInfo> contestSeasonCondition = (root, query, criteriaBuilder) -> criteriaBuilder.isNull(root.get("contestSeason"));
        Specification<ContestantInfo> contestantTypeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestantType"), ContestantType.ORG_VIRTUAL);
        ContestantInfo contestantInfo = contestantInfoRepository.findAll(Specification.where(deletedCondition).and(organizationCondition).and(contestSeasonCondition).and(contestantTypeCondition))
                .stream().findFirst().orElseGet(() -> {
                    ContestantInfo newContestantInfo = new ContestantInfo();
                    newContestantInfo.setOrganization(organization);
                    newContestantInfo.setContestantType(ContestantType.ORG_VIRTUAL);
                    newContestantInfo = contestService.saveContestantInfo(newContestantInfo);
                    return newContestantInfo;
                });

        Contestant contestant = contestantRepository.findByContestantInfoAndContestItemAndDeleted(contestantInfo, contestItem, false).stream().findFirst().orElseGet(() -> {
            Contestant newContestant = new Contestant();
            newContestant.setContestantInfo(contestantInfo);
            newContestant.setContestItem(contestItem);
            newContestant = contestantRepository.save(newContestant);
            return newContestant;
        });
        Works works = new Works();
        works.setContestant(contestant);
        works = loginUserFileService.newUse(loginUserFile, works);
        if (works.getFormat() == Works.WorksFormat.IMG) {
            organizationRepository.nextSummaryOrgVirtualTimeNowIfEmpty(organization.getId());
        }
        return works;
    }

    @EventListener
    @Transactional
    public void handleWorksAuditSuccessEvent(WorksAuditSuccessEvent event) {
        logger.info("WorksAuditSuccessEvent");
        Optional<ContestantInfo> contestantInfo = Optional.ofNullable(event).map(WorksAuditSuccessEvent::getWorks).map(Works::getContestant).map(Contestant::getContestantInfo);
        Boolean isVirtualWorks = contestantInfo
                .map(ContestantInfo::getContestantType)
                .map(t -> t == ContestantType.ORG_VIRTUAL)
                .orElse(false);
        Optional<String> organizationIdOptional = contestantInfo.map(ContestantInfo::getOrganization).map(Organization::getId);
        if (isVirtualWorks && organizationIdOptional.isPresent()) {
            if (event.getWorks().getFormat() == Works.WorksFormat.VIDEO) {
                organizationRepository.nextSummaryOrgVirtualTimeNowIfEmpty(organizationIdOptional.get());
            }
        }
    }

    /**
     * 生成机构注册二维码
     *
     * @param organizationId
     * @param contestSeasonId
     * @param response
     */
    public void qrCode(String organizationId, String contestSeasonId, ServletResponse response) throws MalformedURLException {
        String url = applyUrl(organizationId, contestSeasonId).toString();
        try {

            Organization organization = organizationRepository.findById(organizationId).orElse(null);
            String name = organization == null ? "未知机构" : organization.getName();

            File logoFile = new File(tmpfile, "logo.png");
            if (!logoFile.exists()) {
                FileUtils.copyURLToFile(new URL(logoUrl), logoFile);
            }
            BufferedImage image = QRCodeUtil.createImage(url, name, logoFile.getAbsolutePath(), true);
            response.setContentType("image/jpg");
            ImageIO.write(image, "JPG", response.getOutputStream());
        } catch (WriterException e) {
            e.printStackTrace();
        } catch (IOException e) {
            e.printStackTrace();
        }
    }

    /**
     * 生成报名海报图片
     *
     * @param organizationId
     * @param contestSeasonId
     * @param response
     * @throws MalformedURLException
     */
    public void signUpPost(String organizationId, String contestSeasonId, ServletResponse response) throws MalformedURLException {
        String url = applyUrl(organizationId, contestSeasonId).toString();
        try {
            String name = null;
//            Organization organization = organizationRepository.findById(organizationId).orElse(null);
//            name = organization == null ? "未知机构" : organization.getName();

           /* File logoFile = new File(tmpfile, "logo.png");
            if (!logoFile.exists()) {
                FileUtils.copyURLToFile(new URL(logoUrl), logoFile);
            }*/
            BufferedImage image = QRCodeUtil.createImage(url, name, null, true);

            String backgroundUrl = "https://img.shuyiwa.com/contest/season/postbackground/" + contestSeasonId + ".png";

            BufferedImage background = null;
            try {
                background = ImageIO.read(new URL(backgroundUrl));
            } catch (Exception e) {
                logger.error("背景图片：" + backgroundUrl + " 不存在");
                background = ImageIO.read(new URL(defaultcontBgUrl));
            }
            ImageUtils.overLapImage(background, image, null, null, response);

        } catch (WriterException e) {
            e.printStackTrace();
        } catch (IOException e) {
            e.printStackTrace();
        }
    }


    /**
     * 发送机构审核通过短信
     *
     * @param phone
     */
    public void sendOrgAuditCode(String phone, String orgName) throws FrogException {

        HashMap<String, String> params = new HashMap<>();
        params.put("orgName", orgName);
        smsService.send(phone, params, SMS_216837191);
    }

    public String getOrgRegisterUrl(String organizationId) {
        String url = registerUrl + "?organizationId=" + organizationId;
        return url;
    }

    public URL applyUrl(String organizationId, String contestSeasonId) throws MalformedURLException {
        return new URL(registerUrl + "?organizationId=" + organizationId + "&contestSeasonId=" + contestSeasonId);
    }

    @Autowired
    OrganizationFollowerRepository organizationFollowerRepository;

    @Transactional
    public void followOrganization(String loginUserId, String organizationId) {
        organizationFollowerRepository.saveOrIgnore(organizationId, loginUserId);
        Long count = organizationFollowerRepository.countByOrganizationId(organizationId);
        organizationDataDayRepository.saveOrganizationDataDay(organizationId, OrganizationDataDay.DataType.follower.name(), new BigDecimal(count));
    }

    @Transactional
    public void unFollowOrganization(String loginUserId, String organizationId) {
        organizationFollowerRepository.deleteByOrganizationIdAndLoginUserId(organizationId, loginUserId);
        Long count = organizationFollowerRepository.countByOrganizationId(organizationId);
        organizationDataDayRepository.saveOrganizationDataDay(organizationId, OrganizationDataDay.DataType.follower.name(), new BigDecimal(count));
    }

    public List<OrganizationAdmin> administrators(String organizationId) {
        List<OrganizationAdmin> superAdmin = loginUserAuthorityRepository.findByAuthorityAndEntityId(Authority.SUPER_ADMIN_ORGANIZATION, organizationId)
//                .stream().map(a -> a.getLoginUser())
                .stream().map(a -> {
                    OrganizationAdmin admin = new OrganizationAdmin();
                    admin.setNickName(a.getInEntityNickname());
                    admin.setSuperAdmin(true);
                    admin.setPhone(a.getLoginUser().getPhone());
                    return admin;
                })
                /*.map(loginUser -> {
                    OrganizationAdmin admin = new OrganizationAdmin();
                    admin.setPhone(loginUser.getPhone());
                    admin.setNikeName();
                    admin.setSuperAdmin(true);
                    return admin;
                })*/
                .collect(Collectors.toList());
        List<OrganizationAdmin> normalAdmin = loginUserAuthorityRepository.findByAuthorityAndEntityId(Authority.ADMIN_ORGANIZATION, organizationId)
                .stream()/*.map(a -> a.getLoginUser())
                .map(loginUser -> {
                    OrganizationAdmin admin = new OrganizationAdmin();
                    admin.setPhone(loginUser.getPhone());
                    admin.setSuperAdmin(false);
                    return admin;
                })*/
                .map(a -> {
                    OrganizationAdmin admin = new OrganizationAdmin();
                    admin.setPhone(a.getLoginUser().getPhone());
                    admin.setNickName(a.getInEntityNickname());
                    admin.setSuperAdmin(false);
                    return admin;
                })
                .collect(Collectors.toList());
        List<OrganizationAdmin> adminList = new ArrayList<>();
        adminList.addAll(superAdmin);
        adminList.addAll(normalAdmin);
        return adminList;
    }


    @Transactional
    public Organization settingLogoForOrg(String orgId, String logoFileId) throws FrogException {
        Organization organization = organizationRepository.findById(orgId).orElse(null);
        if (organization == null) {
            throw new FrogException(500, "机构不存在");
        }
        if (logoFileId != null) {
            LoginUserFile loginUserFile = loginUserFileRepository.findById(logoFileId).get();
            if (loginUserFile != null) {
                loginUserFileService.use(loginUserFile, organization);
            }
        }
        if (!StringUtils.isEmpty(organization.getLogo())) {
            LoginUserFile loginUserFile = loginUserFileService.getFromDiskUrl(organization.getLogo());
            if (loginUserFile != null) {
                organization.setLogo(loginUserFileService.upload("organization-logo", loginUserFile));
            }
        }
        organizationRepository.save(organization);
        return organization;
    }

    @Transactional(rollbackFor = Throwable.class)
    public void addAdminForOrganization(String phone, String organizationId, String nickName) {
        LoginUser optionalLoginUser = loginUserRepository.findByPhone(phone).orElse(null);
        if (optionalLoginUser == null) {
            optionalLoginUser = loginUserService.getOrCreateLoginUser(phone, true, null);
        }
        LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
        loginUserAuthority.setAuthority(Authority.ADMIN_ORGANIZATION);
        loginUserAuthority.setEntityId(organizationId);
        loginUserAuthority.setLoginUser(optionalLoginUser);
        loginUserAuthority.setInEntityNickname(nickName);
        loginUserAuthorityRepository.save(loginUserAuthority);
    }

    @Transactional(rollbackFor = Throwable.class)
    public void addMember(String phone, String organizationId, String nickName, String intro, String auth, String avatarId) throws FrogException {
        LoginUser optionalLoginUser = loginUserRepository.findByPhone(phone).orElse(null);
        if (optionalLoginUser == null) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "当前手机号未注册");
        }
        List<LoginUserAuthority> authorityList = loginUserAuthorityRepository.findByLoginUserAndEntityId(optionalLoginUser, organizationId);
        if (authorityList.size() > 0) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "团队成员已存在");
        }
        LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
        if ("ADMIN_ORGANIZATION".equals(auth)) {
            loginUserAuthority.setAuthority(Authority.ADMIN_ORGANIZATION);
        } else if ("COACH".equals(auth)) {
            loginUserAuthority.setAuthority(Authority.COACH);
        } else {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "无权操作");
        }
        optionalLoginUser.setIntroduction(intro);
        optionalLoginUser.setName(nickName);
        if (!StringUtils.isEmpty(avatarId)) {
            loginUserFileService.use(avatarId, optionalLoginUser);
        } else {
            optionalLoginUser = loginUserRepository.save(optionalLoginUser);
        }
        loginUserAuthority.setEntityId(organizationId);
        loginUserAuthority.setLoginUser(optionalLoginUser);
        loginUserAuthority.setInEntityNickname(nickName);
        loginUserAuthorityRepository.save(loginUserAuthority);
    }

    @Autowired
    UserAndCoachRepository userAndCoachRepository;

    @Transactional(rollbackFor = Throwable.class)
    public void updateMember(String id, String organizationId, String nickName, String introduction, boolean isAdmin, boolean isCoach, String avatarId) throws FrogException {
        LoginUser optionalLoginUser = loginUserRepository.findById(id).orElse(null);
        if (optionalLoginUser == null) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "用户不存在");
        }
        //更新教练信息
        optionalLoginUser.setName(nickName);
        optionalLoginUser.setIntroduction(introduction);
        if (!StringUtils.isEmpty(avatarId)) {
            loginUserFileService.use(avatarId, optionalLoginUser);
        } else {
            loginUserRepository.save(optionalLoginUser);
        }
            List<LoginUserAuthority> loginUserAuthorityList = loginUserAuthorityRepository.findByLoginUserAndEntityId(optionalLoginUser,organizationId);
        if (loginUserAuthorityList.size() > 0) {
            LoginUserAuthority loginUserAuthority = loginUserAuthorityList.get(0);
            loginUserAuthority.setInEntityNickname(nickName);
            if (!isAdmin && isCoach) {
                loginUserAuthority.setAuthority(Authority.COACH);
            } else if (!isCoach && isAdmin) {
                loginUserAuthority.setAuthority(Authority.ADMIN_ORGANIZATION);
            }
            loginUserAuthorityRepository.save(loginUserAuthority);
        }
    }

    @Transactional(rollbackFor = Throwable.class)
    public void deleteMember(String id, String organizationId) throws FrogException {
        LoginUser optionalLoginUser = loginUserRepository.findById(id).orElse(null);
        if (optionalLoginUser == null) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "用户不存在");
        }
        //解约教练
        //判断是否已处理完全部消息
        int newsCount = newsRepository.countNewsByLoginUserAndHandleResult(id, organizationId);
        if (newsCount > 0) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "需教练与用户处理全部消息才能解约");
        }
//            //教练存在状态为01345的课程时不能解除
        int appointmentCount = appointmentRepository.countDoing(id, organizationId);
        if (appointmentCount > 0) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "需教练处理完全部课程才能解约");
        }
        int userCount = userAndCoachRepository.countByOrgAndcoach(organizationId, id);
        if (userCount > 0) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "需教练解约完全部用户才能解约");
        }
        //开始解约教练
        loginUserAuthorityRepository.deleteByLoginUserIdAndEntityId(id, organizationId);

        secService.forceLogout(id);

    }
//
//
//        List<LoginUserAuthority> authorityList = loginUserAuthorityRepository.findByLoginUserAndEntityId(optionalLoginUser,organizationId);
//        if(authorityList.size()>0){
//            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"团队成员不可重复添加");
//        }
//        LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
//        if("ADMIN_ORGANIZATION".equals(auth)){
//            loginUserAuthority.setAuthority(Authority.ADMIN_ORGANIZATION);
//        }else if("COACH".equals(auth)){
//            loginUserAuthority.setAuthority(Authority.COACH);
//        }else {
//            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"权限不正确");
//        }
//        optionalLoginUser.setIntroduction(intro);
//        optionalLoginUser = loginUserRepository.save(optionalLoginUser);
//        loginUserAuthority.setEntityId(organizationId);
//        loginUserAuthority.setLoginUser(optionalLoginUser);
//        loginUserAuthority.setInEntityNickname(nickName);
//        loginUserAuthorityRepository.save(loginUserAuthority);
//    }

    @Transactional(rollbackFor = Throwable.class)
    public void updateOrgName(String orgId, String newOrgName) throws FrogException {
        Organization organization = organizationRepository.findById(orgId).orElse(null);
        if (null == organization) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "机构不存在");
        } else {
            organization.setName(newOrgName);
            organizationRepository.save(organization);
        }
    }

    @Transactional(rollbackFor = Throwable.class)
    public void deleteAdminForOrggnization(String phone, String organizationId) {
        LoginUser optionalLoginUser = loginUserRepository.findByPhone(phone).orElse(null);
        if (optionalLoginUser != null) {
            loginUserAuthorityRepository.deleteByLoginUserIdAndEntityId(optionalLoginUser.getId(), organizationId);
        }
    }

    @Transactional(rollbackFor = Throwable.class)
    public void changeSuperAdminForOrggnization(String oldPhone, String newPhone, String organizationId) {
        //更改原超管全部权限
        this.deleteAdminForOrggnization(oldPhone, organizationId);
        this.addAdminForOrganization(oldPhone, organizationId, "");
        //修改新管理员权限
        LoginUser optionalLoginUser = loginUserRepository.findByPhone(newPhone).orElse(null);
        if (optionalLoginUser == null) {
            optionalLoginUser = loginUserService.getOrCreateLoginUser(newPhone, true, null);
        }
        this.deleteAdminForOrggnization(newPhone, organizationId);
        LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
        loginUserAuthority.setAuthority(Authority.SUPER_ADMIN_ORGANIZATION);
        loginUserAuthority.setEntityId(organizationId);
        loginUserAuthority.setLoginUser(optionalLoginUser);
        loginUserAuthority.setInEntityNickname("");
        loginUserAuthorityRepository.save(loginUserAuthority);

    }


    public static class OrganizationAdmin {
        private String phone;
        private boolean superAdmin;
        private String nickName;

        public boolean isSuperAdmin() {
            return superAdmin;
        }

        public void setSuperAdmin(boolean superAdmin) {
            this.superAdmin = superAdmin;
        }

        public String getPhone() {
            return phone;
        }

        public void setPhone(String phone) {
            this.phone = phone;
        }

        public String getNickName() {
            return nickName;
        }

        public void setNickName(String nickName) {
            this.nickName = nickName;
        }
    }
}

package com.shuyiwa.fitness.backend.service;

import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.buffered.BatchBufferWorker;
import com.shuyiwa.fitness.backend.buffered.Bufferable;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.*;
import com.shuyiwa.fitness.backend.event.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.util.DateUtil;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.event.EventListener;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.stream.Collectors;

@Service
public class UserAndCoachService {
    private static final Log logger = LogFactory.getLog(UserAndCoachService.class);

    @Autowired
    LoginUserRepository loginUserRepository;

    @Autowired
    UserAndCoachRepository userAndCoachRepository;
    @Autowired
    NewsService newsService;
    @Autowired
    OrganizationRepository organizationRepository;

    @Autowired
    private ContractRepository contractRepository;

    @Autowired
    UserCoachHistoryService userCoachHistoryService;

    @Autowired
    StoreDataDetailsRepository storeDataDetailsRepository;


    @Transactional
    public void save(String organizationId, LoginUser user, FrogUserDetails frogUserDetails) throws FrogException {
        Organization organization = organizationRepository.findById(organizationId).orElse(null);
        LoginUser coach = frogUserDetails.getLoginUser(loginUserRepository);
        //创建一条用户与教练记录
        UserAndCoach userAndCoach = null;
        if (userAndCoachRepository.searchCount2(organizationId, user.getId()) != 0) {
            userAndCoach = userAndCoachRepository.getRelieved(organizationId, user.getId());
            userAndCoach.setCoach(coach);
            userAndCoach.setStatus(0);
            userAndCoach.setHeadCoachIds(coach.getId());
            userAndCoach.setCreateLoginUser(coach);
            userAndCoach.setVersion(userAndCoach.getVersion() + 1);
            userAndCoach = userAndCoachRepository.save(userAndCoach);

        } else {
            userAndCoach = new UserAndCoach();
            userAndCoach.setStatus(0);
            userAndCoach.setHeadCoachIds(coach.getId());
            userAndCoach.setCreateLoginUser(coach);
            userAndCoach.setOrganization(organization);
            userAndCoach.setCoach(coach);
            userAndCoach.setUser(user);
            userAndCoach = userAndCoachRepository.save(userAndCoach);
        }
        logger.info(coach.getName() + "教练/主管邀请了" + user.getName());
        //创建一条通知
        News news = new News();
        news.setNewsType(NewsType.inviteUser);
        news.setCreateLoginUser(coach);
        news.setReceiveLoginUser(user);
        news.setOrganization(organization);
        news.setNewsBody(coach.getName() + "教练邀请你一起运动！");
        news.setEntityId(userAndCoach.getId());
        news.setHandle_result(0);
        JSONObject json = new JSONObject();
        json.put("coachName", coach.getName());
        news.setContent(json.toJSONString());
        newsService.createNews(news, coach);
        logger.info("创建一条邀请通知：" + news.toString());
    }

    public Page<UserAndCoach> findAllByPage(int page, int size, String orgId, String coachId, String phonestr, Integer sort, Integer sortField) {
        String sortFieldName = "createTime";
        if (sortField == 1){
            sortFieldName = "createTime";
        }
        PageRequest pageRequest = PageRequest.of(page, size, Sort.by("userStatus").descending().and(Sort.by(sortFieldName).descending()));
        if (sort == 2){
            pageRequest = PageRequest.of(page, size, Sort.by("userStatus").descending().and(Sort.by(sortFieldName).ascending()));
        }
        Specification<UserAndCoach> empty = Specification.where(null);
        Specification<UserAndCoach> notDeleted = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
        Specification<UserAndCoach> status = empty;
        if (StringUtils.isEmpty(coachId)) {
            status = (root, query, criteriaBuilder) -> criteriaBuilder.in(root.get("status")).value(0).value(1).value(2).value(3);
        } else {
            status = (root, query, criteriaBuilder) -> criteriaBuilder.in(root.get("status")).value(0).value(1).value(2).value(3);
        }
        //.equal(root.get("status"), 1);
        Specification<UserAndCoach> coachCondition = StringUtils.isEmpty(coachId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("headCoachIds"), "%" + coachId + "%");
        Specification<UserAndCoach> organizationCondition = StringUtils.isEmpty(orgId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), orgId);
        Specification<UserAndCoach> phoneCondition = StringUtils.isEmpty(phonestr) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("user").get("phone"), phonestr + "%");
        Specification<UserAndCoach> nameCondition = StringUtils.isEmpty(phonestr) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("user").get("name"), phonestr + "%");
        //Specification<UserAndCoach> courseNameCondition = StringUtils.isEmpty(phonestr) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("courseName"), "%"+phonestr+"%");

        //        Specification<UserAndCoach> searchCondition = Optional.ofNullable(search).map(String::trim).map(v -> StringUtils.isEmpty(v) ? null : v).map(v -> (Specification<Certificate>) (root, query, criteriaBuilder) ->
//                criteriaBuilder.greaterThan(criteriaBuilder.function("match", Double.class, root.get("search"), new LiteralExpression<String>((CriteriaBuilderImpl) criteriaBuilder, Utils.injectSpace(v))), 0.)
//        ).orElse(empty);

        Specification<LoginUser> notManager = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("isManager"), false);

        Page<UserAndCoach> pageResult = userAndCoachRepository.findAll(Specification
                        .where(notDeleted)
                        .and(status)
                        .and(coachCondition)
                        .and(phoneCondition.or(nameCondition))
                        .and(organizationCondition)
                , pageRequest);

        pageResult.stream().forEach(userAndCoach -> {
            List<Contract> contractList = contractRepository.findByUserId(userAndCoach.getUser().getId(), userAndCoach.getOrganization().getId(),ContractStatus.Contract_NORMAL.getStatus());
            //List<Integer> RemainingClassHoursList = contractList.stream().map(contract -> contract.getRemainingClassHours()).collect(Collectors.toList());
            //userAndCoach.setProperty("RemainingClassHoursList", RemainingClassHoursList);
            userAndCoach.setProperty("validContract",contractList);//有效的合约
        });

        pageResult.stream().forEach(userAndCoach -> {
            List<Appointment> appointmentList = appointmentRepository.findOneHasFinishedCourse(userAndCoach.getUser().getId(), userAndCoach.getOrganization().getId(), AppointmentStatus.FINISH_SUCCESS.getStatus());
            userAndCoach.getProperties().put("lastCourseName", "");
            if (appointmentList.size() > 0) {
                userAndCoach.setLastClassTime(appointmentList.get(0).getCourseStartTime());
                userAndCoach.getProperties().put("lastCourseName", appointmentList.get(0).getCourseName());
            }
//            userAndCoach.put("userName",userAndCoach.getUser()==null?"":loginUserRepository.findById(userAndCoach.getUser().getId()).orElse(null).getName());
            Map<String, String> userMap = new HashMap<>();
            userMap.put("id", userAndCoach.getUser().getId());
            userMap.put("name", userAndCoach.getUser().getName());
            userMap.put("introduction", userAndCoach.getUser().getIntroduction());
            userMap.put("userAvatar", userAndCoach.getUser().getAvatar());
            userMap.put("phone", userAndCoach.getUser().getPhone());
            userMap.put("sex",userAndCoach.getUser().getSex()==null?"":userAndCoach.getUser().getSex().name());
            userMap.put("birthDay", userAndCoach.getUser().getBirthDay()==null?"":DateUtil.toLocalDate(userAndCoach.getUser().getBirthDay()).format(DateTimeFormatter.ofPattern("yyyy-MM-dd")));
            userMap.put("idCard",userAndCoach.getUser().getIdCard());
            userAndCoach.setProperty("userObj", userMap);

            List<Map<String, String>> coachList = new ArrayList<>();
            if (!StringUtils.isEmpty(userAndCoach.getHeadCoachIds())){
                String headCoachIdStr = userAndCoach.getHeadCoachIds();
                String[] headCoachIds = headCoachIdStr.split(",");
                for (String headCoachId : headCoachIds) {
                    LoginUser coach = loginUserRepository.findById(headCoachId).orElse(null);
                    if (coach != null){
                        Map<String, String> coachMap = new HashMap<>();
                        coachMap.put("id", coach.getId());
                        coachMap.put("name", coach.getName());
                        coachMap.put("introduction", coach.getIntroduction());
                        coachMap.put("userAvatar", coach.getAvatar());
                        coachMap.put("phone", coach.getPhone());
                        coachList.add(coachMap);
                    }
                }
            }
//            coachMap.put("id", userAndCoach.getCoach() == null ? "" : userAndCoach.getCoach().getId());
//            coachMap.put("name", userAndCoach.getCoach() == null ? "" : userAndCoach.getCoach().getName());
//            coachMap.put("introduction", userAndCoach.getCoach() == null ? "" : userAndCoach.getCoach().getIntroduction());
//            coachMap.put("userAvatar", userAndCoach.getCoach() == null ? "" : userAndCoach.getCoach().getAvatar());
//            coachMap.put("phone", userAndCoach.getCoach() == null ? "" : userAndCoach.getCoach().getPhone());
            userAndCoach.setProperty("coachObj", coachList);
            //用户的总合约数   totalContractCount
            Integer totalContractCount = contractRepository.totalContractCount(userAndCoach.getUser().getId(), orgId);
            userAndCoach.setProperty("totalContractCount",totalContractCount);
            //用户的当前合约数   currentContractCount
            Integer currentContractCount = contractRepository.currentContractCount(userAndCoach.getUser().getId(), orgId, ContractStatus.Contract_NORMAL.getStatus());
            userAndCoach.setProperty("currentContractCount",currentContractCount);
            //userAndCoach.setProperty("coachObj",loginUserRepository.findById(userAndCoach.getCoach().getId()).orElse(null));

        });
        return pageResult;
    }

    @EventListener
    @Transactional
    @Bufferable(permits = 0, name = "onInviteUserEvent")
    public void onInviteUserEvent(InviteUserEvent event) {

    }

    @Transactional
    @BatchBufferWorker(name = "onInviteUserEvent")
    public void onInviteUserEvents(List<InviteUserEvent> events) {
        for (InviteUserEvent event : events) {
            logger.info("InviteUserEvent: " + event.getEntityId());
            if ("1".equals(event.getStatus())) {
                userAndCoachRepository.findById(event.getEntityId()).ifPresent(userAndCoach -> {
                    logger.info("onInviteUserEvent：同意邀请" + userAndCoach.getId() + " :::" + event.getStatus());
//                    userAndCoach.setStatus(1);
//                    userAndCoachRepository.save(userAndCoach);
                    userAndCoachRepository.updateStatusById(1, userAndCoach.getCoach().getId(), event.getEntityId());
                    userCoachHistoryService.save(event.getEntityId(),userAndCoach.getCoach().getId(),userAndCoach.getOrganization().getId(),userAndCoach.getCoach().getId());
                });
            } else if ("2".equals(event.getStatus())) {
                userAndCoachRepository.findById(event.getEntityId()).ifPresent(userAndCoach -> {
                    logger.info("onInviteUserEvent 拒绝邀请：" + userAndCoach.getId() + " :::" + event.getStatus());
                    //userAndCoach.setStatus(4);//拒绝邀请或已解约
//                    userAndCoachRepository.save(userAndCoach);
                    userAndCoachRepository.updateStatusById(4, userAndCoach.getCoach().getId(), event.getEntityId());
                });
            }
        }
    }

    @EventListener
    @Transactional
    @Bufferable(permits = 0, name = "onAppointmentHandleEvent")
    public void onAppointmentHandleEvent(AppointmentHandleEvent event) {

    }

    @Autowired
    AppointmentRepository appointmentRepository;

    @Transactional
    @BatchBufferWorker(name = "onAppointmentHandleEvent")
    public void onAppointmentHandleEvents(List<AppointmentHandleEvent> events) {
        for (AppointmentHandleEvent event : events) {
            logger.info("AppointmentHandleEvent: " + event.getEntityId());
            if ("1".equals(event.getStatus())) {
                appointmentRepository.findById(event.getEntityId()).ifPresent(appointment -> {
                    logger.info("onAppointmentHandleEvent 同意预约课程：" + appointment.getId() + " :::" + event.getStatus());
                    appointment.setStatus(AppointmentStatus.APPOINTMENT_SUCCESS.getStatus());
                    appointmentRepository.save(appointment);
                });
            } else if ("2".equals(event.getStatus())) {
                appointmentRepository.findById(event.getEntityId()).ifPresent(appointment -> {
                    logger.info("onAppointmentHandleEvent 预约课程被拒绝" + appointment.getId() + " :::" + event.getStatus());
                    appointment.setDeleted(true);
                    appointmentRepository.save(appointment);
                    UserAndCoach userAndCoach = userAndCoachRepository.findByOrganizationAndUser(appointment.getOrganization(), appointment.getUser()).orElse(null);
                    if (userAndCoach != null) {
                        logger.info(appointment.getUser().getId() + " 预约课程被拒绝，返还已扣除课程余额");
                        String amount = appointment.getAmount();
                        int updatenum = userAndCoachRepository.backupAmount(appointment.getOrganization().getId(), appointment.getUser().getId(), StringUtils.isEmpty(amount) ? 0 : Integer.valueOf(amount), userAndCoach.getVersion());
                        logger.info("拒绝约课[" + event.getEntityId() + "]后更新用户[" + userAndCoach.getUser().getId() + "]余额结果：" + updatenum);
                    }
                });
            }
        }
    }


    @EventListener
    @Transactional
    @Bufferable(permits = 0, name = "onFinishClassHandleEvent")
    public void onFinishClassHandleEvent(FinishClassHandleEvent event) {

    }

    @Transactional
    @BatchBufferWorker(name = "onFinishClassHandleEvent")
    public void onFinishClassHandleEvents(List<FinishClassHandleEvent> events) {
        for (FinishClassHandleEvent event : events) {
            logger.info("FinishClassHandleEvent: " + event.getEntityId());
            if ("1".equals(event.getStatus())) {
                appointmentRepository.findById(event.getEntityId()).ifPresent(appointment -> {
                    logger.info("onFinishClassHandleEvent 同意核销课程 " + appointment.getId() + " :::" + event.getStatus());
                    appointment.setStatus(AppointmentStatus.FINISH_SUCCESS.getStatus());//已核销
                    appointment.setConfirmTime(new Date());
                    Appointment appointment1 = appointmentRepository.save(appointment);
                    StoreDataDetails storeDataDetails = new StoreDataDetails();
                    storeDataDetails.setType(StoreDataDetailsStatus.OTHER.getStatus());
                    Contract contract = contractRepository.findById(appointment1.getContractId()).get();
                    storeDataDetails.setDataId(appointment1.getId());
                    storeDataDetails.setBehavior(StoreDataBehaviorType.finishAppointment.name());
                    storeDataDetails.setExecNum(1);
                    storeDataDetails.setExecAmount(contract.getTotalAmount() / contract.getClassHour());
                    storeDataDetails.setRevenueAmount(0);
                    storeDataDetails.setCoachIds(appointment1.getCoach().getId());
                    //保存店铺数据详情
                    storeDataDetailsRepository.save(storeDataDetails);
                });
            } else if ("2".equals(event.getStatus())) {
                appointmentRepository.findById(event.getEntityId()).ifPresent(appointment -> {
                    logger.info("onFinishClassHandleEvent 拒绝核销课程 " + appointment.getId() + " :::" + event.getStatus());
                    appointment.setStatus(AppointmentStatus.FINISH_FAIL.getStatus());//拒绝核销
                    appointmentRepository.save(appointment);
                });
            }
        }
    }

//    @Autowired
//    LoginUserRepository loginUserRepository;

    @EventListener
    @Transactional
    @Bufferable(permits = 0, name = "onChangeClassHandleEvent")
    public void onChangeClassHandleEvent(ChangeClassHandleEvent event) {

    }

    @Transactional
    @BatchBufferWorker(name = "onChangeClassHandleEvent")
    public void onChangeClassHandleEvents(List<ChangeClassHandleEvent> events) {
        /*for (ChangeClassHandleEvent event : events) {
            logger.info("ChangeClassHandleEvent: " + event.getEntityId());

            String content = event.getContent();
            JSONObject json = JSONObject.parseObject(content);

            if ("1".equals(event.getStatus())) {
                Appointment appointment = appointmentRepository.findById(event.getEntityId()).orElse(null);
                if (appointment != null) {
                    if (json.containsKey("isChangeCourse") && "1".equals(json.getString("isChangeCourse"))) {
                        Appointment newAppointment = new Appointment();
                        if (json.containsKey("courseName")) {
                            newAppointment.setCourseName(json.getString("courseName"));
                        }

                        if (json.containsKey("courseId")) {
                            newAppointment.setCourseId(json.getString("courseId"));
                        }
                        newAppointment.setPayType("point");
                        newAppointment.setCoach(appointment.getCoach());
                        newAppointment.setUser(appointment.getUser());
                        newAppointment.setOrganization(appointment.getOrganization());
                        newAppointment.setCreateLoginUser(appointment.getCreateLoginUser());
                        newAppointment.setLastUpdateTime(new Date());
                        if (json.containsKey("coursePrice")) {
                            newAppointment.setAmount(json.getString("coursePrice"));
                        }
                        if (json.containsKey("courseStartTime")) {
                            //newAppointment.setCourseStartTime(json.getDate("courseStartTime"));
                        }
                        if (json.containsKey("courseEndTime")) {
                            newAppointment.setCourseEndTime(json.getDate("courseEndTime"));
                        }
                        if (json.containsKey("tempCoach")) {//设置代课教练
                            newAppointment.setTempCoach(loginUserRepository.findById(json.getString("tempCoach")).orElse(null));
                        }
                        newAppointment.setStatus(AppointmentStatus.APPOINTMENT_SUCCESS.getStatus());
                        //保存新课程
                        appointmentRepository.save(newAppointment);

                        UserAndCoach userAndCoach = userAndCoachRepository.findByOrganizationAndUser(appointment.getOrganization(), appointment.getUser()).orElse(null);
                        userAndCoach.setAmount(userAndCoach.getAmount() + Integer.valueOf(appointment.getAmount()));
                        //退还老课程的花费
                        userAndCoachRepository.save(userAndCoach);

                        //删除预约的老数据
                        appointment.setStatus(AppointmentStatus.APPOINTMENT_SUCCESS.getStatus());
                        appointment.setDeleted(true);
                        appointment.setReamrk(newAppointment.getId());
                        appointmentRepository.save(appointment);

                    } else {
                        if (json.containsKey("courseStartTime")) {
                            appointment.setCourseStartTime(json.getDate("courseStartTime"));
                        }
                        if (json.containsKey("courseEndTime")) {
                            appointment.setCourseEndTime(json.getDate("courseEndTime"));
                        }
                        if (json.containsKey("tempCoach")) {//设置代课教练
                            appointment.setTempCoach(loginUserRepository.findById(json.getString("tempCoach")).orElse(null));
                        }
                        appointment.setStatus(AppointmentStatus.APPOINTMENT_SUCCESS.getStatus());

                        appointmentRepository.save(appointment);
                    }

                } else {
                    logger.info("预约课程" + event.getEntityId() + " 不存在");
                }
            } else if ("2".equals(event.getStatus())) {
                appointmentRepository.findById(event.getEntityId()).ifPresent(appointment -> {
                    logger.info("ChangeClassHandleEvent 拒绝改课 " + appointment.getId() + " :::" + event.getStatus());
                    appointment.setStatus(AppointmentStatus.APPOINTMENT_SUCCESS.getStatus());//改课被拒绝，状态由预约中改为预约成功
                    appointmentRepository.save(appointment);
                    Integer amount = 0;
                    if (json.containsKey("coursePrice")) {
                        amount = json.getInteger("coursePrice");
                    }
                    if (json.containsKey("isChangeCourse") && "1".equals(json.getString("isChangeCourse")) && amount > 0) {
                        logger.info("拒绝用户[" + appointment.getUser().getId() + "]改课[" + event.getEntityId() + "]返还改课消费:" + amount);
                        UserAndCoach userAndCoach = userAndCoachRepository.findByOrganizationAndUser(appointment.getOrganization(), appointment.getUser()).orElse(null);
                        userAndCoach.setAmount(userAndCoach.getAmount() + amount);
                        userAndCoachRepository.save(userAndCoach);
                        logger.info("返还余额后用户[" + appointment.getUser().getId() + "]当前余额:" + userAndCoach.getAmount());
                    }
                });
            }
        }*/
    }

    @EventListener
    @Transactional
    @Bufferable(permits = 0, name = "onChangeCoachEvent")
    public void onChangeCoachEventEvent(ChangeCoachEvent event) {

    }

    @Transactional
    @BatchBufferWorker(name = "onChangeCoachEvent")
    public void onChangeCoachEvents(List<ChangeCoachEvent> events) {
        logger.info("ChangeCoachEvent 换教练 " );
        /*for (ChangeCoachEvent event : events) {
            logger.info("ChangeCoachEvent: " + event.getEntityId());
            if ("1".equals(event.getStatus())) {
                userAndCoachRepository.findById(event.getEntityId()).ifPresent(userAndCoach -> {
                    logger.info("ChangeCoachEvent 同意换教练 " + userAndCoach.getId() + " :::" + event.getStatus());
                    String content = event.getContent();
                    JSONObject json = JSONObject.parseObject(content);
                    if (json.containsKey("newCoachId")) {
                        userAndCoach.setCoach(loginUserRepository.findById(json.getString("newCoachId")).orElse(null));
                    }
                    userAndCoach.setStatus(1);
                    userAndCoachRepository.save(userAndCoach);
                });
            } else if ("2".equals(event.getStatus())) {
                userAndCoachRepository.findById(event.getEntityId()).ifPresent(userAndCoach -> {
                    logger.info("ChangeCoachEvent 拒绝换教练 " + userAndCoach.getId() + " :::" + event.getStatus());
                    if (3 != userAndCoach.getStatus()) {
                        userAndCoach.setStatus(1);//拒绝
                        userAndCoachRepository.save(userAndCoach);
                    }
                });
            }
        }*/
    }


    @EventListener
    @Transactional
    @Bufferable(permits = 0, name = "onUnviteUserEvent")
    public void onUnviteUserEvent(UnviteUserEvent event) {

    }

    @Transactional
    @BatchBufferWorker(name = "onUnviteUserEvent")
    public void onUnviteUserEvents(List<UnviteUserEvent> events) {
        for (UnviteUserEvent event : events) {
            logger.info("UnviteUserEvent: " + event.getEntityId());
            if ("1".equals(event.getStatus())) {
                userAndCoachRepository.findById(event.getEntityId()).ifPresent(userAndCoach -> {
                    logger.info("onUnviteUserEvent 同意解约 " + event.getEntityId() + " :::" + event.getStatus());
                    userAndCoach.setStatus(4);//确认和教练解约
                    userAndCoachRepository.save(userAndCoach);
                });
            } else if ("2".equals(event.getStatus())) {
                logger.info("onUnviteUserEvent 拒绝解约 " + event.getEntityId() + " :::" + event.getStatus());
                //   userAndCoachRepository.findById(event.getEntityId()).ifPresent(userAndCoach -> {
//                    userAndCoach.setDeleted(true);
                //   userAndCoachRepository.save(userAndCoach);
                // });
            }
        }
    }


    public Map<String, Integer> signatoryCount(String organizationId, String coachId) {
        Map<String, Integer> map = new HashMap<>();
        //签约人数
        Specification<Contract> empty = Specification.where(null);
        Specification<Contract> organizationCondition = StringUtils.isEmpty(organizationId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), organizationId);
        Specification<Contract> deletedCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), 0);
        List<Contract> contractList = contractRepository.findAll(Specification.where(organizationCondition).and(deletedCondition));
        int signatoryCount = 0;
        for (Contract contract : contractList) {
            if (!StringUtils.isEmpty(contract.getSignatoryId())) {
                if (contract.getSignatoryId().contains(coachId)) {
                    signatoryCount++;
                }
            }
        }
        map.put("signatoryCount", signatoryCount);
        //作为主教练
        Specification<UserAndCoach> empty1 = Specification.where(null);
        Specification<UserAndCoach> organizationCondition1 = StringUtils.isEmpty(organizationId) ? empty1 : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), organizationId);
        Specification<UserAndCoach> coachIdCondition = StringUtils.isEmpty(coachId) ? empty1 : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("coach").get("id"), coachId);
        int headCoachCount = userAndCoachRepository.findAll(
                Specification.where(organizationCondition1)
                        .and(coachIdCondition)).size();
        map.put("headCoachCount", headCoachCount);
        return map;
    }
}

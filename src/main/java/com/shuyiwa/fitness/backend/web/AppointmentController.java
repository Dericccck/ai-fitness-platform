package com.shuyiwa.fitness.backend.web;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.channel.ChannelService;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.sec.FrogUserDetailsService;
import com.shuyiwa.fitness.backend.service.AppointmentService;
import com.shuyiwa.fitness.backend.service.ContractService;
import com.shuyiwa.fitness.backend.service.NewsService;
import org.apache.commons.lang3.time.DateFormatUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.apache.poi.hssf.usermodel.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.*;


import javax.servlet.ServletOutputStream;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.*;

@RestController
public class AppointmentController {

    private static final Log logger = LogFactory.getLog(AppointmentController.class);

    @Autowired
    AppointmentService appointmentService;
    @Autowired
    UserAndCoachRepository userAndCoachRepository;
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    OrganizationRepository organizationRepository;
    @Autowired
    NewsService newsService;
    @Autowired
    AppointmentRepository appointmentRepository;

    @Autowired
    ChannelService channelService;

    @Autowired
    CourseRepository courseRepository;

    @Autowired
    ContractService contractService;

    @Autowired
    ContractRepository contractRepository;

    @Autowired
    VacationRecordRepository vacationRecordRepository;

    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;

    @Autowired
    NewsRepository newsRepository;

    @Autowired
    StoreDataDetailsRepository storeDataDetailsRepository;

    @Autowired
    private SystemSettingsRepository systemSettingsRepository;

    private SimpleDateFormat smf = new SimpleDateFormat("yyyy-MM-dd HH:mm");
    private SimpleDateFormat smf2 = new SimpleDateFormat("HH:mm");

    /**
     * 约课
     * 条件:
     * // 合约是否有效1.合约有效期，2课时
     * //课程是否开启
     */
    @RuntimeDoc(client = {RuntimeDoc.Client.Api, RuntimeDoc.Client.Console}, desc = "约课")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/fitness/appointment", method = RequestMethod.POST)
    Appointment userAppointment(
            @RequestBody JSONObject jsonObject,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {

        String organizationId = jsonObject.getString("organizationId");
        String studentId = jsonObject.getString("studentId");
        String contractId = jsonObject.getString("contractId");
        String startDateStr = jsonObject.getString("startDate");
        String startTimeStr = jsonObject.getString("startTime");
        String coachId = jsonObject.getString("coachId");
        Integer mark = jsonObject.getInteger("mark");

        if (StringUtils.isEmpty(organizationId)) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "机构id必填");
        }
        if (StringUtils.isEmpty(studentId)) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "学员id必填");
        }
        if (StringUtils.isEmpty(contractId)) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "请选择课程");
        }
        if (StringUtils.isEmpty(startDateStr) || StringUtils.isEmpty(startTimeStr)) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "请选择课程时间");
        }
        if (StringUtils.isEmpty(coachId)) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "请选择上课教练");
        }

        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        LoginUser student = loginUserRepository.findById(studentId).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "学员不存在"));

        LoginUser classCoach = loginUserRepository.findById(coachId).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "教练不存在"));

        List<LoginUserAuthority> authorityList = loginUserAuthorityRepository.findByLoginUserAndEntityId(classCoach, organizationId);
        long isCoachOrAdmin = authorityList.stream().
                filter(loginUserAuthority -> (loginUserAuthority.getAuthority() == Authority.ADMIN_ORGANIZATION || loginUserAuthority.getAuthority() == Authority.COACH))
                .count();
        if (isCoachOrAdmin < 1) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "该教练不为此机构教练，无法约课");
        }

        Optional<Organization> organization = organizationRepository.findById(organizationId);
        Optional<UserAndCoach> userAndCoach = userAndCoachRepository.findByOrganizationAndUser(organization.get(), student);
        /*if(1 != userAndCoach.get().getStatus()){
            if(2 == userAndCoach.get().getStatus()){
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"该用户更换教练中，需确认后操作");
            }else if(3 == userAndCoach.get().getStatus()){
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"该用户解约教练中，需确认后操作");
            }else if(4 == userAndCoach.get().getStatus()){
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"该用户已与教练解约");
            }else if (0 == userAndCoach.get().getStatus()){
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"用户与教练关系未确认");
            }
        }*/
        Date startDate = jsonObject.getDate("sshizhtartDate");
        if (null == userAndCoach.get()) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "学员未签约，无法约课");
        }
        String headCoachIds = userAndCoach.get().getHeadCoachIds();
        if (null == headCoachIds) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "学员未指定教练，无法约课");
        }
        if (1 != userAndCoach.get().getUserStatus()) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "学员账号被冻结，无法约课");
        }

        String dateTimeStr = startDateStr + " " + startTimeStr;

        Date dateTime = null;
        try {
            dateTime = smf.parse(dateTimeStr);
        } catch (ParseException e) {
            e.printStackTrace();
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "开始时间处理异常");
        }
        if (dateTime.before(new Date())) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "预约时间不能早于当前时间");
        }

        //判断机构今天是否为休息日
        List<SystemSettings> systemSettingsList = systemSettingsRepository.findByTypeAndOrganization(SystemSettingEnum.Nonbusiness_Day, organization.get());
        if (systemSettingsList != null && systemSettingsList.size() > 0) {
            for (SystemSettings systemSettings : systemSettingsList) {
                String body = systemSettings.getBody();
                List<String> dateList = JSON.parseObject(body, List.class);
                Date startDB = JSON.parseObject(dateList.get(dateList.size() - 1), Date.class);
                Calendar tempStartDB = Calendar.getInstance();
                tempStartDB.setTime(startDB);
                Date endDB = JSON.parseObject(dateList.get(0), Date.class);
                Calendar tempEndDB = Calendar.getInstance();
                tempEndDB.setTime(endDB);
                tempStartDB.add(Calendar.DAY_OF_YEAR, -1);
                tempEndDB.add(Calendar.DAY_OF_YEAR, 1);
                if (tempStartDB.getTime().before(startDate) && tempEndDB.getTime().after(startDate)) {
                    throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "非营业日，无法约课");
                }
            }
        }
        //判断教练是否休假
        List<VacationRecord> vacationRecordList = null;
        vacationRecordList = vacationRecordRepository.findByOrganizationAndCoachIdAndStatus(organization.get(), classCoach.getId(), VacationStatus.Vacation_NOTCANCEL.getStatus());
        if (vacationRecordList != null && vacationRecordList.size() > 0) {
            for (VacationRecord vacationRecord : vacationRecordList) {
                Date startDB = vacationRecord.getStartDate();
                Calendar tempStartDB = Calendar.getInstance();
                tempStartDB.setTime(startDB);
                Date endDB = vacationRecord.getEndDate();
                Calendar tempEndDB = Calendar.getInstance();
                tempEndDB.setTime(endDB);
                tempStartDB.add(Calendar.DAY_OF_YEAR, -1);
                tempEndDB.add(Calendar.DAY_OF_YEAR, 1);
                if (tempStartDB.getTime().before(startDate) && tempEndDB.getTime().after(startDate)) {
                    throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "该教练正在休假，无法约课");
                }
            }
        }

        Course course = null;
        Contract contract = contractService.findById(contractId);
        if (contract == null || ContractStatus.Contract_NORMAL.getStatus() != contract.getStatus()) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程不存在或合约已失效");
        } else if (contract.getContractCreateTime().after(startDate) || contract.getContractEndTime().before(startDate)) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "该课程不在合约计划时间范围内");
        } else if (contract.getRemainingClassHours() < 1) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课时不足，无法约课");
        } else {
            String courseId = contract.getCourseId();
            course = courseRepository.findById(courseId).orElse(null);
            if (course == null || course.getStatus() != 1) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "预约课程不存在或已下线");
            }
        }

        Appointment appointment = new Appointment();
        appointment.setMark(mark);
        appointment.setStatus(AppointmentStatus.APPOINTMENT_SUCCESS.getStatus());
        appointment.setDeleted(false);
        appointment.setCreateLoginUser(loginUser);
        appointment.setLastUpdateLoginUser(loginUser);
        appointment.setOrganization(organization.get());
        appointment.setCoach(classCoach);
        appointment.setUser(student);
        appointment.setCourseName(course.getName());
        appointment.setCourseStartDate(startDate);
        appointment.setCourseStartTime(dateTime);
        appointment.setContractId(contractId);
        appointment.setCourseId(course.getId());
        appointment.setHeadCoachIds(userAndCoach.get().getHeadCoachIds());

        int author = 0;
        Boolean isAdmin = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .filter(authority -> authority == Authority.ADMIN_ORGANIZATION)
                .count() > 0;
        if (isAdmin) {
            author = 1;
        } else {
            Boolean isCOACH = frogUserDetails.getAuthorities().stream()
                    .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                    .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                    .map(a -> a.getAuthorityEnum())
                    .filter(authority -> authority == Authority.COACH)
                    .count() > 0;
            if (isCOACH) {
                author = 2;
            }
        }
        appointment = appointmentService.saveAppointment(contract, appointment);

        createNews(appointment, loginUser, author);
        return appointment;
    }


    /**
     * //创建通知
     * 1.用户约课：发送给教练，
     * 2。教练约课：发给用户，如果选择的是代课教练，同时发给代课教练
     * 3.主管约课：发给教练和用户，（如果选择的是代课教练，发给代课教练）
     *
     * @param appointment 约课信息
     * @param loginUser   当前登录人
     * @param author      当前登录人的身份 0：用户 1：管理员 2：教练
     */
    private void createNews(Appointment appointment, LoginUser loginUser, int author) {
        News news = null;
        if (0 == author) { //用户   给上课教练发  处理人上课教练
            news = new News(loginUser, appointment.getCoach(), NewsType.appointments, "", appointment.getId(), appointment.getOrganization(), "您有一条约课信息，请查看处理");
            news.setHandle_result(1);
            news.setHandleTime(new Date());
            LoginUser handleUser = appointment.getCoach();
            news.setHandleUserId(handleUser.getId());
            newsService.createNews(news, loginUser);
        } else if (2 == author) {  //教练   给用户发
            news = new News(loginUser, appointment.getUser(), NewsType.appointments, "", appointment.getId(), appointment.getOrganization(), "您有一条约课信息，请查看处理");
            news.setHandle_result(1);
            news.setHandleTime(new Date());
            news.setHandleUserId(appointment.getUser().getId());
            newsService.createNews(news, loginUser);
        } else if (1 == author) {  //管理员  给用户发
            news = new News(loginUser, appointment.getUser(), NewsType.appointments, "", appointment.getId(), appointment.getOrganization(), "您有一条约课信息，请查看处理");
            news.setHandle_result(1);
            news.setHandleTime(new Date());
            news.setHandleUserId(appointment.getUser().getId());
            newsService.createNews(news, loginUser);
            news = new News(loginUser, appointment.getCoach(), NewsType.appointments, "", appointment.getId(), appointment.getOrganization(), "您有一条约课信息，请查看处理");
            news.setHandle_result(1);
            news.setHandleTime(new Date());
            LoginUser handleUser = appointment.getCoach();
            news.setHandleUserId(handleUser.getId());
            newsService.createNews(news, loginUser);
        }

    }


    @RuntimeDoc(client = {RuntimeDoc.Client.Api, RuntimeDoc.Client.Console}, desc = "查看约课列表")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/fitness/search/page/appointment", method = RequestMethod.GET)
    public Page<Appointment> appointmentPage(
            @RequestParam(value = "organizationId") String organizationId,
            @RequestParam(value = "userId", required = false, defaultValue = "") String userId,
            @RequestParam(value = "coachId", required = false, defaultValue = "") String coachId,
            @RequestParam int page, @RequestParam int size,
            @RequestParam(value = "ifHistory", required = false, defaultValue = "0") int ifHistory,
            @RequestParam(value = "date", required = false, defaultValue = "") String date,
            @RequestParam(value = "search", required = false, defaultValue = "") String search,
            @RequestParam(value = "mark", required = true) Integer mark,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails, HttpServletResponse response
    ) throws FrogException, ParseException {
        //查询全部课程或历史课程
        Boolean isAdmin = false;
        isAdmin = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .filter(authority -> authority == Authority.ADMIN_ORGANIZATION)
                .count() > 0;
        String loginUserId = frogUserDetails.getLoginUserId();
        Page<Appointment> page1 = appointmentService.appointmentPage2(organizationId, userId, coachId, ifHistory, date, page, size, isAdmin, loginUserId, search, null, null, mark);
        page1.stream().forEach(appointment -> {
            //UserAndCoach userAndCoach = userAndCoachRepository.findByOrganizationAndUser(appointment.getOrganization(),appointment.getUser()).get();
            //appointment.getProperties().put("remarkUserName",userAndCoach==null?"":userAndCoach.getRemarkUserName());
            appointment.getProperties().put("userName", appointment.getUser() == null ? "" : appointment.getUser().getName());
            appointment.getProperties().put("userPhone", appointment.getUser() == null ? "" : appointment.getUser().getPhone());
            appointment.getProperties().put("userCreateTime", appointment.getUser() == null ? "" : appointment.getUser().getCreateTime());
            appointment.getProperties().put("coachName", appointment.getCoach() == null ? "" : appointment.getCoach().getName());
            appointment.getProperties().put("appointmentLoginUserName", appointment.getLastUpdateLoginUser() == null ? "" : appointment.getLastUpdateLoginUser().getName());

            String headCoachName = "";
            if (!StringUtils.isEmpty(appointment.getHeadCoachIds())) {
                String[] headCoachIds = appointment.getHeadCoachIds().split(",");
                int i = 0;
                for (String headCoachId : headCoachIds) {
                    LoginUser coach = loginUserRepository.findById(headCoachId).orElse(null);
                    if (coach != null) {
                        if (i == 0) {
                            headCoachName = headCoachName + coach.getName();
                        } else {
                            headCoachName = headCoachName + "、" + coach.getName();
                        }
                    }
                    i++;
                }
            }
            appointment.getProperties().put("headCoachName", headCoachName);
        });
        return page1;
    }


    @RuntimeDoc(client = {RuntimeDoc.Client.Api, RuntimeDoc.Client.Console}, desc = "改课")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/fitness/appointment/change/{id}", method = RequestMethod.POST)
    Appointment changeAppointment(@PathVariable("id") String id,
                                  @RequestBody JSONObject jsonObject,
                                  @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        logger.info("用户" + frogUserDetails.getLoginUserId() + " changeAppointment：" + id + ";" + jsonObject.toJSONString());
        Appointment appointment = appointmentRepository.findById(id).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程不存在"));
        if (AppointmentStatus.FINISHING.getStatus() == appointment.getStatus() || AppointmentStatus.FINISH_SUCCESS.getStatus() == appointment.getStatus()) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "此状态下不准改课");
        } else if (appointment.isDeleted()) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "预约课程不存在");
        }

        String contractId = jsonObject.getString("contractId");
        Date startDate = jsonObject.getDate("startDate");
        String startDateStr = jsonObject.getString("startDate");
        String startTimeStr = jsonObject.getString("startTime");
        String classCoachId = jsonObject.getString("coachId");
        if (StringUtils.isEmpty(classCoachId)) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "请选择上课教练");
        }
        LoginUser classCoach = loginUserRepository.findById(classCoachId).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "教练不存在"));

        String dateTimeStr = startDateStr + " " + startTimeStr;

        if (StringUtils.isEmpty(startDateStr) || StringUtils.isEmpty(startTimeStr)) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "请选择课程时间");
        }
        Date dateTime = null;
        try {
            dateTime = smf.parse(dateTimeStr);
        } catch (ParseException e) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "开始时间处理异常");
        }
        if (dateTime.before(new Date())) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "预约时间不能早于当前时间");
        }

        Organization organization = appointment.getOrganization();
        //判断是否为非营业日
        List<SystemSettings> systemSettingsList = systemSettingsRepository.findByTypeAndOrganization(SystemSettingEnum.Nonbusiness_Day, organization);
        if (systemSettingsList != null && systemSettingsList.size() > 0) {
            for (SystemSettings systemSettings : systemSettingsList) {
                String body = systemSettings.getBody();
                List<String> dateList = JSON.parseObject(body, List.class);
                Date startDB = JSON.parseObject(dateList.get(dateList.size() - 1), Date.class);
                Calendar tempStartDB = Calendar.getInstance();
                tempStartDB.setTime(startDB);
                Date endDB = JSON.parseObject(dateList.get(0), Date.class);
                Calendar tempEndDB = Calendar.getInstance();
                tempEndDB.setTime(endDB);
                tempStartDB.add(Calendar.DAY_OF_YEAR, -1);
                tempEndDB.add(Calendar.DAY_OF_YEAR, 1);
                if (tempStartDB.getTime().before(startDate) && tempEndDB.getTime().after(startDate)) {
                    throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "非营业日，无法约课");
                }
            }
        }
        //判断教练是否休假
        List<VacationRecord> vacationRecordList = null;
        vacationRecordList = vacationRecordRepository.findByOrganizationAndCoachIdAndStatus(organization, classCoach.getId(), VacationStatus.Vacation_NOTCANCEL.getStatus());
        if (vacationRecordList != null && vacationRecordList.size() > 0) {
            for (VacationRecord vacationRecord : vacationRecordList) {
                Date startDB = vacationRecord.getStartDate();
                Calendar tempStartDB = Calendar.getInstance();
                tempStartDB.setTime(startDB);
                Date endDB = vacationRecord.getEndDate();
                Calendar tempEndDB = Calendar.getInstance();
                tempEndDB.setTime(endDB);
                tempStartDB.add(Calendar.DAY_OF_YEAR, -1);
                tempEndDB.add(Calendar.DAY_OF_YEAR, 1);
                if (tempStartDB.getTime().before(startDate) && tempEndDB.getTime().after(startDate)) {
                    throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "该教练正在休假，无法约课");
                }
            }
        }

        //UserAndCoach userAndCoach = userAndCoachRepository.findByOrganizationAndUser(appointment.getOrganization(), appointment.getUser()).orElse(null);
        if (appointment.getContractId().equals(contractId)) {
            appointment.setCoach(classCoach);
            appointment.setCourseStartDate(startDate);
            appointment.setLastUpdateLoginUser(frogUserDetails.getLoginUser(loginUserRepository));
            appointment.setCourseStartTime(dateTime);
            appointment.setLastUpdateTime(new Date());
            appointment = appointmentRepository.save(appointment);
        } else {
            String oldContractId = appointment.getContractId();
            Contract oldContract = contractService.findById(oldContractId);
            Course course = null;
            Contract contract = contractService.findById(contractId);
            if (contract == null || ContractStatus.Contract_NORMAL.getStatus() != contract.getStatus()) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程不存在或合约已失效");
            } else if (contract.getContractCreateTime().after(startDate) || contract.getContractEndTime().before(startDate)) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "该课程不在计划时间范围内");
            } else if (contract.getRemainingClassHours() < 1) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课时不足，无法约课");
            } else {
                String courseId = contract.getCourseId();
                course = courseRepository.findById(courseId).orElse(null);
                if (course == null || course.getStatus() != 1) {
                    throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "预约课程不存在或已下线");
                }
            }
            appointment.setLastUpdateTime(new Date());
            appointment.setCourseStartTime(dateTime);
            appointment.setCourseStartDate(startDate);
            appointment.setCoach(classCoach);
            appointment.setLastUpdateLoginUser(frogUserDetails.getLoginUser(loginUserRepository));
            appointment.setContractId(contractId);
            appointment.setCourseId(course.getId());
            appointment.setCourseName(course.getName());
            appointment = appointmentService.saveAppointment(contract, appointment);

            oldContract.setRemainingClassHours(oldContract.getRemainingClassHours() + 1);
            contractRepository.save(oldContract);
            appointmentService.handleAppointmentByContractId(appointment.getContractId(), appointment.getCreateTime());
        }

        boolean isCoach = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .filter(authority -> (authority == Authority.COACH || authority == Authority.ADMIN_ORGANIZATION))
                .count() > 0;

        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        LoginUser receiveUser = appointment.getCoach();
        Map<String, String> map = new HashMap<>();

        map.put("coachName", appointment.getCoach().getName());
        //教练改课  用户发        非教练 给上课教练发
        News news = new News(loginUser, receiveUser, NewsType.changeClass, JSONObject.toJSONString(map), appointment.getId(), appointment.getOrganization(), loginUser.getName() + "修改了约课信息");
        if (isCoach) {
            receiveUser = appointment.getUser();
            news.setReceiveLoginUser(receiveUser);
        }
        news.setHandle_result(1);
        news.setHandleTime(new Date());
        news.setHandleUserId(loginUser.getId());
        newsService.createNews(news, loginUser);

        return appointment;

    }

    /**
     * 1.管理员后台登录核销，直接核销
     * 2.用户小程序端核销，直接核销
     * 3.教练小程序端核销，发消息让用户确认核销
     * 4.管理员作为教练小程序端核销，发消息让用户确认核销
     * @param id
     * @param callFrom (miniparam or backend)
     * @param frogUserDetails
     *
     * @return
     * @throws FrogException
     */
    @RuntimeDoc(client = {RuntimeDoc.Client.Api, RuntimeDoc.Client.Console}, desc = "核销课程")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/fitness/appointment/finish/{id}", method = RequestMethod.POST)
    Appointment finishAppointment(@PathVariable("id") String id,@RequestParam(name = "callFrom")String callFrom,
                                  @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        if(StringUtils.isEmpty(callFrom)){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"参数 callFrom 必填");
        }
        Appointment appointment = appointmentRepository.findById(id).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程不存在"));
        boolean isAdmin = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .filter(authority -> (authority == Authority.ADMIN_ORGANIZATION))
                .count() > 0;
        boolean isCoach = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .filter(authority -> (authority == Authority.COACH))
                .count() > 0;
        String newsId = null;
        if (AppointmentStatus.WAITINGFOR_FINISHCLASS.getStatus() != appointment.getStatus()
                && AppointmentStatus.FINISH_FAIL.getStatus() != appointment.getStatus()
                && AppointmentStatus.FINISHING.getStatus() != appointment.getStatus()) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "此状态下禁止核销课程");
        } else if (appointment.getStatus() == AppointmentStatus.FINISHING.getStatus()) {
            if (isAdmin && "backend".equals(callFrom)){
                newsId = newsRepository.findByNewsTypeAndEntityId(NewsType.finishClass.name(), appointment.getId());
                if (!StringUtils.isEmpty(newsId)) {
                    newsService.updateNews(newsId, frogUserDetails.getLoginUser(loginUserRepository), 1);
                    appointment.setStatus(AppointmentStatus.FINISH_SUCCESS.getStatus());
                    appointment.setLastApplyUserId(appointment.getUser().getId());
                    appointment.setConfirmTime(new Date());
                }
            } else {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "用户无权限");
            }
        } else {
            appointment.setStatus(AppointmentStatus.FINISHING.getStatus());//核销中
            appointment.setLastUpdateLoginUser(frogUserDetails.getLoginUser(loginUserRepository));
            LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
            LoginUser receiveUser = appointment.getCoach();

            Optional<UserAndCoach> userAndCoach = userAndCoachRepository.findByOrganizationAndUser(appointment.getOrganization(), appointment.getUser());

            News news = new News(loginUser, receiveUser, NewsType.finishClass, "", appointment.getId(), appointment.getOrganization(), loginUser.getName() + "，课程" + appointment.getCourseName() + "已完成");
            if (isCoach || (isAdmin && "miniparam".equals(callFrom))) {
                //教练、管理员作为教练在小程序端核销需要生成确认消息给用户
                receiveUser = appointment.getUser();
                if (null != userAndCoach) {
                    appointment.setLastApplyUserId(userAndCoach.get().getCoach().getId());
                }
                news.setNewsBody("训练课程 " + appointment.getCourseName() + " 已结束，请您确认！");
                news.setHandle_result(0);
                news.setReceiveLoginUser(receiveUser);
                newsService.createNews(news, loginUser);
            } else if((!isCoach && !isAdmin && "miniparam".equals(callFrom))
                    || (isAdmin && "backend".equals(callFrom))){
                //用户、管理员核销直接结束
                appointment.setStatus(AppointmentStatus.FINISH_SUCCESS.getStatus());
                appointment.setLastApplyUserId(appointment.getUser().getId());
                appointment.setConfirmTime(new Date());
//                news.setReceiveLoginUser(appointment.getUser());
                news.setHandle_result(1);
                news.setHandleTime(new Date());
                news.setHandleUserId(frogUserDetails.getLoginUserId());
                newsService.createNews(news, loginUser);

                if (isAdmin && "backend".equals(callFrom)) { //管理员通过后台服务核销课程，需要发消息给用户

                    news = new News(loginUser, receiveUser, NewsType.finishClass, "", appointment.getId(), appointment.getOrganization(), loginUser.getName() + "，课程" + appointment.getCourseName() + "已完成");
                    news.setReceiveLoginUser(appointment.getUser());
                    news.setHandle_result(1);
                    news.setHandleTime(new Date());
                    news.setHandleUserId(frogUserDetails.getLoginUserId());
                    newsService.createNews(news, loginUser);
                }
            }
        }

        Appointment appointment1 = appointmentRepository.save(appointment);

        //1.用户核销时，2.管理员在后台直接核销时，需要添加数据到店铺详情表
        if ((!isCoach && !isAdmin && "miniparam".equals(callFrom))
                || (isAdmin && "backend".equals(callFrom) && StringUtils.isEmpty(newsId))){
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
        }

        return appointment1;
    }

    @Deprecated
    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "强制核销课程", sinceTime = "2021-08-03")
    @RequestMapping(value = "api/fitness/appointment/forcefinish/{id}", method = RequestMethod.POST)
    void forceFinish(@PathVariable String id,
                     @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        Appointment appointment = appointmentRepository.findById(id).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程不存在"));
        if (AppointmentStatus.WAITINGFOR_FINISHCLASS.getStatus() == appointment.getStatus()
                || AppointmentStatus.FINISH_FAIL.getStatus() == appointment.getStatus()
                || AppointmentStatus.FINISHING.getStatus() == appointment.getStatus()) {
            if (AppointmentStatus.FINISHING.getStatus() == appointment.getStatus()) {
                newsService.forceUPdateNews(1, frogUserDetails.getLoginUserId(), NewsType.finishClass.name(), id);
            }
            appointment.setStatus(AppointmentStatus.FINISH_SUCCESS.getStatus());
            appointment.setLastUpdateLoginUser(frogUserDetails.getLoginUser(loginUserRepository));
            appointmentRepository.save(appointment);
        } else {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "此状态下禁止核销课程");
        }
    }


    /**
     * 1.判断课程是否存在
     * 2.判断课程预约状态
     * 3.退还金额
     * 4.修改状态，同时删除相关消息
     * 5.记录谁取消预约的，时间点
     *
     * @param id
     * @param frogUserDetails
     * @throws FrogException
     */
    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "取消预约课程", sinceTime = "2021-08-03")
    @RequestMapping(value = "api/fitness/appointment/cancel/{id}", method = RequestMethod.POST)
    void cancelAppointment(@PathVariable String id,
                           @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        Appointment appointment = appointmentRepository.findById(id).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "预约课程不存在"));
        if (AppointmentStatus.FINISHING.getStatus() == appointment.getStatus() || AppointmentStatus.FINISH_SUCCESS.getStatus() == appointment.getStatus() || appointment.isDeleted()) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程已核销，无法取消预约");
        }
        if (appointment.isDeleted()) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程已取消预约");
        }
        logger.info("api/fitness/appointment/cancel [" + frogUserDetails.getLoginUserId() + "]取消预约： " + id);

        appointmentService.cancelAppointment(appointment, frogUserDetails.getLoginUser(loginUserRepository));

        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        Map<String, String> map = new HashMap<>();
        String coachName = appointment.getCoach().getName();
        map.put("coachName", coachName);
        map.put("courseName", appointment.getCourseName());
        News news = new News(loginUser, appointment.getCoach(), NewsType.cancelClass, JSONObject.toJSONString(map), appointment.getId(), appointment.getOrganization(), loginUser.getName() + "，课程" + appointment.getCourseName() + "已取消");
        news.setHandle_result(1);
        news.setHandleUserId(loginUser.getId());
        news.setHandleTime(new Date());
        newsService.createNews(news, loginUser);
    }


    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/fitness/appointment/export", method = RequestMethod.GET)
    public void export(@RequestParam(value = "organizationId") String organizationId,
                       @RequestParam(value = "userId", required = false, defaultValue = "") String userId,
                       @RequestParam(value = "coachId", required = false, defaultValue = "") String coachId,
                       @RequestParam(value = "ifHistory", required = false, defaultValue = "0") int ifHistory,
                       @RequestParam(value = "date", required = false, defaultValue = "") String date,
                       @RequestParam(value = "dateType", required = false) Integer dateType,
                       @RequestParam(value = "contractId", required = false) String contractId,
                       @RequestParam(value = "search", required = false, defaultValue = "") String search,
                       @AuthenticationPrincipal FrogUserDetails frogUserDetails,
                       HttpServletRequest request, HttpServletResponse response) throws FrogException {
        Boolean isAdmin = false;
        isAdmin = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .filter(authority -> authority == Authority.ADMIN_ORGANIZATION)
                .count() > 0;
        String loginUserId = frogUserDetails.getLoginUserId();
        try {
            Page<Appointment> page1 = appointmentService.appointmentPage2(organizationId, userId, coachId, ifHistory, date, 0, 1000, isAdmin, loginUserId, search, dateType, contractId, 0);

            String fileName = "教练%s预约课程.xls";
            if (!StringUtils.isEmpty(coachId)) {
                LoginUser user = loginUserRepository.findById(coachId).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "教练不存在"));
                fileName = String.format(fileName, user.getName());
            }
            channelService.setResponseFileName(request, response, fileName);

            HSSFWorkbook workbook = new HSSFWorkbook();
            HSSFSheet sheet = workbook.createSheet("预约课程");
            String[] headers = {"日期", "开始", "合约编号", "课程", "状态", "余课", "课时费", "结算后余额", "用户", "约课", "上课"};

            HSSFRow row = sheet.createRow(0);
            for (int i = 0; i < headers.length; i++) {
                HSSFCell cell = row.createCell(i);
                HSSFRichTextString text = new HSSFRichTextString(headers[i]);
                cell.setCellValue(text);
            }
            List<Appointment> dataList = page1.getContent();

            for (int i = 0; i < dataList.size(); i++) {
                row = sheet.createRow(i + 1);
                Appointment appointment = dataList.get(i);
                String appointDate = StringUtils.isEmpty(appointment.getCourseStartTime()) ? "" : DateFormatUtils.format(appointment.getCourseStartTime(), "yyyy-MM-dd");
                createCell(row, 0, appointDate);
                String appointDateStart = StringUtils.isEmpty(appointment.getCourseStartTime()) ? "" : DateFormatUtils.format(appointment.getCourseStartTime(), "HH:00");
//                String appointDateEnd = StringUtils.isEmpty(appointment.getCourseEndTime()) ? "" : DateFormatUtils.format(appointment.getCourseEndTime(), "HH:00");
                createCell(row, 1, appointDateStart);
//                createCell(row, 2, appointDateEnd);
                createCell(row, 2, appointment.getContract().getNumberId());
                createCell(row, 3, appointment.getCourseName());
                if (appointment.isDeleted()) {
                    createCell(row, 4, "取消预约");
                } else {
                    createCell(row, 4, AppointmentStatus.getAppointmentName(appointment.getStatus()));
                }
                createCell(row, 5, appointment.getContract().getRemainingClassHours().toString());
                Double realPrice = 1.0 * appointment.getContract().getTotalAmount() / appointment.getContract().getClassHour();
                BigDecimal bg = new BigDecimal(realPrice).setScale(1, RoundingMode.HALF_UP);
                createCell(row, 6, bg.toString());
                Double balance = bg.doubleValue() * appointment.getContract().getRemainingClassHours();
                BigDecimal bg1 = new BigDecimal(balance).setScale(1, RoundingMode.HALF_UP);
                createCell(row, 7, bg1.toString());
                List<UserAndCoach> user = userAndCoachRepository.findByUser(appointment.getUser().getId());
                createCell(row, 8, user == null ? "" : user.get(0).getUser().getName());
                createCell(row, 9, appointment.getLastUpdateLoginUser() == null ? "" : appointment.getLastUpdateLoginUser().getName());
                createCell(row, 10, appointment.getCoach() == null ? "" : appointment.getCoach().getName());
            }

            try (ServletOutputStream outputStream = response.getOutputStream()) {
                workbook.write(outputStream);
            }

        } catch (ParseException e) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "数据出错");
        } catch (IOException e) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "导出出错");
        }


    }


    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/fitness/appointment/user/export", method = RequestMethod.GET)
    public void userExport(@RequestParam(value = "organizationId") String organizationId,
                           @RequestParam(value = "userId", required = false, defaultValue = "") String userId,
                           @RequestParam(value = "coachId", required = false, defaultValue = "") String coachId,
                           @RequestParam(value = "ifHistory", required = false, defaultValue = "0") int ifHistory,
                           @RequestParam(value = "date", required = false, defaultValue = "") String date,
                           @RequestParam(value = "dateType", required = false) Integer dateType,
                           @RequestParam(value = "contractId", required = false) String contractId,
                           @RequestParam(value = "search", required = false, defaultValue = "") String search,
                           @AuthenticationPrincipal FrogUserDetails frogUserDetails,
                           HttpServletRequest request, HttpServletResponse response) throws FrogException {
        Boolean isAdmin = false;
        isAdmin = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .filter(authority -> authority == Authority.ADMIN_ORGANIZATION)
                .count() > 0;
        String loginUserId = frogUserDetails.getLoginUserId();
        try {
            Page<Appointment> page1 = appointmentService.appointmentPage2(organizationId, userId, coachId, ifHistory, date, 0, 1000, isAdmin, loginUserId, search, dateType, contractId, 0);

            String fileName = "客户%s预约课程.xls";
            if (!StringUtils.isEmpty(userId)) {
                LoginUser user = loginUserRepository.findById(userId).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "用户不存在"));
                fileName = String.format(fileName, user.getName());
            }

            channelService.setResponseFileName(request, response, fileName);

            HSSFWorkbook workbook = new HSSFWorkbook();
            HSSFSheet sheet = workbook.createSheet("预约课程");
            String[] headers = {"日期", "开始", "合约编号", "课程", "状态", "余课", "课时费", "结算后余额", "约课", "上课"};

            HSSFRow row = sheet.createRow(0);
            for (int i = 0; i < headers.length; i++) {
                HSSFCell cell = row.createCell(i);
                HSSFRichTextString text = new HSSFRichTextString(headers[i]);
                cell.setCellValue(text);
            }
            List<Appointment> dataList = page1.getContent();

            for (int i = 0; i < dataList.size(); i++) {
                row = sheet.createRow(i + 1);
                Appointment appointment = dataList.get(i);
                String appointDate = StringUtils.isEmpty(appointment.getCourseStartTime()) ? "" : DateFormatUtils.format(appointment.getCourseStartDate(), "yyyy-MM-dd");
                createCell(row, 0, appointDate);
//                String appointDateStart = "";//appointment.getCourseStartTime();
                String appointDateStart = StringUtils.isEmpty(appointment.getCourseStartTime()) ? "" : DateFormatUtils.format(appointment.getCourseStartTime(), "HH:00");
//                String appointDateEnd = "";
//                String appointDateEnd = StringUtils.isEmpty(appointment.getCourseEndTime()) ? "" : DateFormatUtils.format(appointment.getCourseEndTime(), "HH:00");
                createCell(row, 1, appointDateStart);
//                createCell(row, 2, appointDateEnd);
                createCell(row, 2, appointment.getContract().getNumberId());
                createCell(row, 3, appointment.getCourseName());
                if (appointment.isDeleted()) {
                    createCell(row, 4, "取消预约");
                } else {
                    createCell(row, 4, AppointmentStatus.getAppointmentName(appointment.getStatus()));
                }
                createCell(row, 5, appointment.getContract().getRemainingClassHours().toString());
                Double realPrice = 1.0 * appointment.getContract().getTotalAmount() / appointment.getContract().getClassHour();
                BigDecimal bg = new BigDecimal(realPrice).setScale(1, RoundingMode.HALF_UP);
                createCell(row, 6, bg.toString());
                Double balance = bg.doubleValue() * appointment.getContract().getRemainingClassHours();
                BigDecimal bg1 = new BigDecimal(balance).setScale(1, RoundingMode.HALF_UP);
                createCell(row, 7, bg1.toString());
                List<UserAndCoach> userAndCoachList = userAndCoachRepository.findByUser(appointment.getUser().getId());
                createCell(row, 8, appointment.getLastUpdateLoginUser() == null ? "" : appointment.getLastUpdateLoginUser().getName());
                createCell(row, 9, appointment.getCoach() == null ? "" : appointment.getCoach().getName());
            }

            try (ServletOutputStream outputStream = response.getOutputStream()) {
                workbook.write(outputStream);
            }
        } catch (ParseException e) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "数据出错");
        } catch (IOException e) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "导出出错");
        }


    }


    private void createCell(HSSFRow row, int index, String value) {
        HSSFCell cell = row.createCell(index);
        HSSFRichTextString text = new HSSFRichTextString(value);
        cell.setCellValue(text);
    }


    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "今日统计数据", sinceTime = "2022-02-24")
    @RequestMapping(value = "api/fitness/appointment/statistics", method = RequestMethod.GET)
    Map<String, Integer> statisticsToday(
            @RequestParam(value = "organizationId") String organizationId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        Map<String, Integer> resultMap = appointmentService.statisticsToday(organizationId);
        return resultMap;
    }


    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "总上课数", sinceTime = "2022-02-24")
    @RequestMapping(value = "api/fitness/appointment/totalClassHour", method = RequestMethod.GET)
    List<Map<String, Object>> totalClassHour(
            @RequestParam(value = "organizationId") String organizationId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "days", required = false, defaultValue = "7") Integer days
    ) {
        List<Map<String, Object>> totalStatistics = appointmentService.totalClassHour(days, organizationId);
        return totalStatistics;
    }


    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "教练上课数", sinceTime = "2022-02-25")
    @RequestMapping(value = "api/fitness/appointment/coach/classHour", method = RequestMethod.GET)
    List<Map<String, Object>> totalClass(
            @RequestParam(value = "organizationId") String organizationId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "coachId", required = false) String coachId,
            @RequestParam(value = "days", required = false, defaultValue = "7") Integer days
    ) {
        List<Map<String, Object>> list = null;
        if (StringUtils.isEmpty(coachId)) {//查询全部教练
            list = appointmentService.allCoach(organizationId, days);
        } else { //查询单个教练
            list = appointmentService.singleCoach(organizationId, days, coachId);
        }
        return list;
    }


    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "修改课时", sinceTime = "2022-03-02")
    @RequestMapping(value = "api/fitness/appointment/update/classHour", method = RequestMethod.POST)
    void updateClassHour(
            @RequestBody JSONObject jsonObject,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) {
        String organizationId = jsonObject.getString("organizationId");
        String coachId = jsonObject.getString("coachId");
        Date date = jsonObject.getDate("date");
        appointmentService.updateClassHour(organizationId, coachId, date);
    }

    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "更新课时统计表数据", sinceTime = "2022-06-20")
    @RequestMapping(value = "api/fitness/appointment/update/classHourStatistics", method = RequestMethod.POST)
    void updateClassHourStatistics(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) {
        appointmentService.updateClassHourStatistics();
    }
}

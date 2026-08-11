package com.shuyiwa.fitness.backend.service;

import com.alibaba.fastjson.JSON;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.domain.dict.NewsType;
import com.shuyiwa.fitness.backend.domain.dict.VacationStatus;
import com.shuyiwa.fitness.backend.global.FrogException;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.text.DateFormat;
import java.text.SimpleDateFormat;
import java.util.*;


@Service
public class SystemSettingsService {
    private static final Log logger = LogFactory.getLog(SystemSettingsService.class);

    @Autowired
    private SystemSettingsRepository systemSettingsRepository;

    @Autowired
    private AppointmentRepository appointmentRepository;

    @Autowired
    private LoginUserAuthorityRepository loginUserAuthorityRepository;

    @Autowired
    private LoginUserRepository loginUserRepository;

    @Autowired
    private OrganizationRepository organizationRepository;

    @Autowired
    private VacationRecordRepository vacationRecordRepository;

    @Autowired
    private NewsService newsService;

    public void holiday(VacationRecord vacationRecord, LoginUser creator) {
        DateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");
        Date start = vacationRecord.getStartDate();
        Date end = vacationRecord.getEndDate();
        List<String> days = new ArrayList();
        Calendar tempStart = Calendar.getInstance();
        tempStart.setTime(start);
        Calendar tempEnd = Calendar.getInstance();
        tempEnd.setTime(end);
        tempEnd.add(Calendar.DATE, +1);
        while (tempStart.before(tempEnd)) {
            String format = sdf.format(tempStart.getTime());
            days.add(format);
            tempStart.add(Calendar.DAY_OF_YEAR, 1);
        }

        if (StringUtils.isEmpty(vacationRecord.getId())){
            //新增
            List<VacationRecord> vacationRecordList = vacationRecordRepository.findByOrganizationAndCoachIdAndStatus(vacationRecord.getOrganization(), vacationRecord.getCoachId(), VacationStatus.Vacation_NOTCANCEL.getStatus());
            if (vacationRecordList != null && vacationRecordList.size() > 0){
                for (VacationRecord vacationRecordDB : vacationRecordList) {
                    List<String> daysDB = new ArrayList();
                    Date startDB = vacationRecordDB.getStartDate();
                    Date endDB = vacationRecordDB.getEndDate();
                    Calendar tempStartDB = Calendar.getInstance();
                    tempStartDB.setTime(startDB);
                    Calendar tempEndDB = Calendar.getInstance();
                    tempEndDB.setTime(endDB);
                    tempEndDB.add(Calendar.DATE, +1);
                    while (tempStartDB.before(tempEndDB)) {
                        String format = sdf.format(tempStartDB.getTime());
                        daysDB.add(format);
                        tempStartDB.add(Calendar.DAY_OF_YEAR, 1);
                    }
                    for (int i = 0; i < days.size(); i++) {
                        for (int j = 0; j < daysDB.size(); j++) {
                            if (days.get(i).equals(daysDB.get(j))){
                                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "选择日期在请假表中已存在");
                            }
                        }
                    }
                }
            }
            vacationRecord.setCreateTime(new Date());
            vacationRecord.setCreateLoginUser(creator);
            vacationRecord.setStatus(VacationStatus.Vacation_NOTCANCEL.getStatus());
            vacationRecordRepository.save(vacationRecord);

            List<Appointment> appointmentList = appointmentRepository.findByCoach(vacationRecord.getCoachId(),vacationRecord.getStartDate(),vacationRecord.getEndDate(),vacationRecord.getOrganization().getId());
            List<LoginUserAuthority> loginUserAuthorityList = loginUserAuthorityRepository.findByAuthorityAndEntityId(Authority.ADMIN_ORGANIZATION, vacationRecord.getOrganization().getId());
            if (appointmentList != null && appointmentList.size() > 0){
                for (Appointment appointment : appointmentList) {
                    LoginUser loginUser = null;
                    if(loginUserAuthorityList != null && loginUserAuthorityList.size()>0){
                        loginUser = loginUserAuthorityList.get(0).getLoginUser();
                        if (vacationRecord.getCoachId().equals(loginUser.getId()) && loginUserAuthorityList.size() > 1){
                            loginUser = loginUserAuthorityList.get(1).getLoginUser();
                        }
                    }
                    //休假人为教练
                    if(vacationRecord.getCoachId().equals(appointment.getCoach().getId())){
                        appointment.setCoach(loginUser);
                        appointmentRepository.save(appointment);
                        createNews(appointment,creator);
                    }
//                    //他为代课教练
//                    if(vacationRecord.getCoachId().equals(appointment.getTempCoach().getId())){
//                        appointment.setTempCoach(loginUser);
//                        appointmentRepository.save(appointment);
//                        createNews(appointment,creator);
//                    }
                }
            }
        } else {
            List<VacationRecord> vacationRecordList = vacationRecordRepository.findByOrganizationAndCoachIdAndStatus(vacationRecord.getOrganization(), vacationRecord.getCoachId(), VacationStatus.Vacation_NOTCANCEL.getStatus());
            if (vacationRecordList != null && vacationRecordList.size() > 0){
                for (VacationRecord vacationRecordDB : vacationRecordList) {
                    if (!vacationRecord.getId().equals(vacationRecordDB.getId())){
                        List<String> daysDB = new ArrayList();
                        Date startDB = vacationRecordDB.getStartDate();
                        Date endDB = vacationRecordDB.getEndDate();
                        Calendar tempStartDB = Calendar.getInstance();
                        tempStartDB.setTime(startDB);
                        Calendar tempEndDB = Calendar.getInstance();
                        tempEndDB.setTime(endDB);
                        tempEndDB.add(Calendar.DATE, +1);
                        while (tempStartDB.before(tempEndDB)) {
                            String format = sdf.format(tempStartDB.getTime());
                            daysDB.add(format);
                            tempStartDB.add(Calendar.DAY_OF_YEAR, 1);
                        }
                        for (int i = 0; i < days.size(); i++) {
                            for (int j = 0; j < daysDB.size(); j++) {
                                if (days.get(i).equals(daysDB.get(j))){
                                    throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "选择日期在请假表中已存在");
                                }
                            }
                        }
                    }
                }
            }
            VacationRecord vacationRecordDB = vacationRecordRepository.findById(vacationRecord.getId()).get();
            vacationRecordDB.setEndDate(vacationRecord.getEndDate());
            vacationRecordDB.setStartDate(vacationRecord.getStartDate());
            vacationRecordRepository.save(vacationRecordDB);

            List<Appointment> appointmentList = appointmentRepository.findByCoach(vacationRecordDB.getCoachId(),vacationRecordDB.getStartDate(),vacationRecordDB.getEndDate(),vacationRecordDB.getOrganization().getId());
            List<LoginUserAuthority> loginUserAuthorityList = loginUserAuthorityRepository.findByAuthorityAndEntityId(Authority.ADMIN_ORGANIZATION, vacationRecordDB.getOrganization().getId());
            if (appointmentList != null && appointmentList.size() > 0){
                for (Appointment appointment : appointmentList) {
                    LoginUser loginUser = null;
                    if(loginUserAuthorityList != null && loginUserAuthorityList.size()>0){
                        loginUser = loginUserAuthorityList.get(0).getLoginUser();
                        if (vacationRecord.getCoachId().equals(loginUser.getId()) && loginUserAuthorityList.size() > 1){
                            loginUser = loginUserAuthorityList.get(1).getLoginUser();
                        }
                    }
                    //休假人为教练
                    if(vacationRecordDB.getCoachId().equals(appointment.getCoach().getId())){
                        appointment.setCoach(loginUser);
                        appointmentRepository.save(appointment);
                        createNews(appointment,creator);
                    }
                    //他为代课教练
//                    if(vacationRecordDB.getCoachId().equals(appointment.getTempCoach().getId())){
//                        appointment.setTempCoach(loginUser);
//                        appointmentRepository.save(appointment);
//                        createNews(appointment,creator);
//                    }
                }
            }
        }
    }

    public void createNews(Appointment appointment, LoginUser loginUser) {
            News news = new News(loginUser, loginUser, NewsType.appointments, "", appointment.getId(), appointment.getOrganization(), "您有一条约课信息，请查看处理");
            news.setHandle_result(1);
            news.setHandleTime(new Date());
            news.setHandleUserId(loginUser.getId());
            newsService.createNews(news, loginUser);
    }

    public void cancelHoliday(String id,String organizationId) {
        VacationRecord vacationRecord = vacationRecordRepository.findById(id).orElse(null);
        if (vacationRecord != null){
            vacationRecord.setStatus(VacationStatus.Vacation_CANCEL.getStatus());
            vacationRecordRepository.save(vacationRecord);
        }
    }

    //请假列表
    public List<VacationRecord> holidayList(String organizationId, String coachId, Integer days, Boolean isAdmin) {
        Optional<Organization> organization = organizationRepository.findById(organizationId);
        List<VacationRecord> vacationRecordList = new ArrayList<>();
        if (isAdmin){
            //管理员看全部
            if (days != null){
                if(StringUtils.isEmpty(coachId)){
                    vacationRecordList = vacationRecordRepository.findByDaysAndOrganization(days,organization.get());
                    getAppointmentList(organization, vacationRecordList);
                } else {
                    vacationRecordList = vacationRecordRepository.findByDaysAndOrganizationAndCoachId(days,organization.get(),coachId);
                    getAppointmentList(organization, vacationRecordList);
                }
            } else {
                if(StringUtils.isEmpty(coachId)){
                    vacationRecordList = vacationRecordRepository.findByOrganization(organization.get());
                    getAppointmentList(organization, vacationRecordList);
                } else {
                    vacationRecordList = vacationRecordRepository.findByOrganizationAndCoachIdAndCreateTime(organization.get(),coachId);
                    getAppointmentList(organization, vacationRecordList);
                }
            }
        } else {
            //教练只可看自己
            vacationRecordList = vacationRecordRepository.findByOrganizationAndCoachId(organization.get(),coachId);
        }
        return vacationRecordList;
    }

    private void getAppointmentList(Optional<Organization> organization, List<VacationRecord> vacationRecordList) {
        if (vacationRecordList != null && vacationRecordList.size() > 0){
            for (VacationRecord vacationRecord : vacationRecordList) {
                LoginUser loginUser = loginUserRepository.findById(vacationRecord.getCoachId()).orElse(null);
                vacationRecord.setProperty("vacationPerson","");
                if (loginUser != null){
                    vacationRecord.setProperty("vacationPerson",loginUser.getName());
                }
                List<Appointment> appointmentListDB = appointmentRepository.findByCoach(vacationRecord.getCoachId(),vacationRecord.getStartDate(),vacationRecord.getEndDate(), organization.get().getId());
                List<Map<String,Object>> appointmentList = new ArrayList<>();
                if (appointmentListDB != null && appointmentListDB.size() > 0){
                    for (Appointment appointment : appointmentListDB) {
                        Map<String,Object> map = new HashMap<>();
                        map.put("id",appointment.getId());
                        map.put("courseStartTime",appointment.getCourseStartTime());
                        map.put("userName",appointment.getUser().getName());
                        map.put("courseName",appointment.getCourseName());
                        map.put("userPhone",appointment.getUser().getPhone());
                        appointmentList.add(map);
                    }
                }
                //vacationRecord.setProperty("appointmentList",appointmentList);
            }
        }
    }
}

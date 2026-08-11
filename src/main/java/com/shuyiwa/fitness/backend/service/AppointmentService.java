package com.shuyiwa.fitness.backend.service;

import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.AppointmentStatus;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.domain.dict.NewsType;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.apache.commons.lang3.time.DateFormatUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.stream.Stream;

@Service
public class AppointmentService {
    private static final Log logger = LogFactory.getLog(AppointmentService.class);

    @Autowired
    AppointmentRepository appointmentRepository;

    @Autowired
    NewsRepository newsRepository;

    @Autowired
    NewsService newsService;

    @Autowired
    NewsWechatRepository newsWechatRepository;

    @Autowired
    UserAndCoachRepository userAndCoachRepository;

    @Autowired
    CourseRepository courseRepository;

    @Autowired
    ContractService contractService;

    @Autowired
    ContractRepository contractRepository;

    @Autowired
    ClassHourStatisticsRepository classHourStatisticsRepository;

    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;

    /**
     * 扣减合约课时，创建约课信息
     */
    @Transactional(rollbackFor = Throwable.class)
    public Appointment saveAppointment(Contract contract,Appointment appointment ){
        contract = contractService.findById(contract.getId());
        if(contract.getRemainingClassHours()<1){
            logger.warn(contract.toString());
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"课时数不足，无法预约");
        }
        contract.setRemainingClassHours(contract.getRemainingClassHours()-1);
        contractRepository.save(contract);
        appointment.setAmount(contract.getRemainingClassHours()+"");
        appointment  = createAppointment(appointment);
        return appointment;
    }

    /**
     * 创建约课信息
     */
    public Appointment createAppointment(Appointment appointment){
        return appointmentRepository.save(appointment);
    }

    /**
     * 约课：减去课时数
     */
    @Transactional(rollbackFor = Throwable.class)
    public void appointmentClass(String organizationId,String userId,Integer coursePrice) throws FrogException {
        //减课，乐观锁
        Map<String,Integer> classHourAndVersion = userAndCoachRepository.getAmount(organizationId,userId);
        if(classHourAndVersion.get("amount")-coursePrice > 0){
            userAndCoachRepository.minusOne(organizationId,userId,classHourAndVersion.get("version"),coursePrice);
        }else {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"用户余额不足");
        }
    }


    @Transactional(rollbackFor = Throwable.class)
    public void cancelAppointment(Appointment appointment,LoginUser loginUser){
        Contract contract = contractService.findById(appointment.getContractId());
        contract.setRemainingClassHours(contract.getRemainingClassHours()+1);
        contractRepository.save(contract);

        appointment.setLastUpdateLoginUser(loginUser);
        appointment.setDeleted(true);
        JSONObject json = new JSONObject();
        json.put("operator",loginUser.getId());
        json.put("operatorTime",System.currentTimeMillis());
        json.put("operation_type","cancel");
        appointment.setReamrk(json.toJSONString());
        appointmentRepository.save(appointment);

        newsService.deleteNewsByEntityId(appointment.getId());
        handleAppointmentByContractId(appointment.getContractId(), appointment.getCreateTime());

    }

    public void handleAppointmentByContractId(String contractId,Date createTime){
        List<Appointment> list = appointmentRepository.findAllByContractId(contractId,createTime);
        if(list.size()>0){
            list.forEach(appointment -> appointment.setAmount((Integer.parseInt(appointment.getAmount())+1)+""));
            appointmentRepository.saveAll(list);
        }
    }

    /**
     * 查看全部课程
     */
    @Transactional(rollbackFor = Throwable.class)
    public Page<Appointment> appointmentPage2(String organizationId, String userId, String coachId,int ifHistory,String date,
                                              int page, int size, Boolean isAdmin,String loginUserId,String search, Integer dateType, String contractId, Integer mark) throws FrogException, ParseException {
        PageRequest pageRequest = PageRequest.of(page, size, Sort.by("courseStartTime").descending());
        Specification<Appointment> empty = Specification.where(null);
        Specification<Appointment> notDeleted = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
        Specification<Appointment> org = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"),organizationId);
        Specification<Appointment> user = empty;
        Specification<Appointment> coach = empty;
        Specification<Appointment> history = empty;
        Specification<Appointment> today = empty;
        Specification<Appointment> userCondition = empty;
        Specification<Appointment> startDate = empty;
        Specification<Appointment> contractCondition = StringUtils.isEmpty(contractId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contractId"),contractId);;
        if(!StringUtils.isEmpty(dateType)){
            Date startTime = new Date();
            Calendar calc =Calendar.getInstance();
            calc.setTime(new Date());
            if(dateType.equals(1)){
                //30天
                calc.add(calc.DATE,-30);
                startTime = calc.getTime();
            } else if (dateType.equals(2)){
                //90天
                calc.add(calc.DATE, -90);
                startTime = calc.getTime();
            } else if (dateType.equals(3)){
                //一年
                calc.add(calc.YEAR,-1);
                startTime = calc.getTime();
            }
            Date finalStartTime = startTime;
            startDate = StringUtils.isEmpty(finalStartTime) ? empty :(root, query, criteriaBuilder) -> criteriaBuilder.between(root.get("courseStartTime"), finalStartTime,new Date());
        }
        if(1 == ifHistory){
            history = (root, query, criteriaBuilder) -> criteriaBuilder.lessThan(root.get("courseStartTime"),new Date());
        }
        if(!StringUtils.isEmpty(date)){
            SimpleDateFormat formatter = new SimpleDateFormat( "yyyy-MM-dd");
            Date someDay = formatter.parse(date);

            Calendar calendar = Calendar.getInstance();
            calendar.setTime(someDay);
            calendar.add(Calendar.DATE,0);
            Date todayD = calendar.getTime();
            calendar.add(Calendar.DATE,1);
            Date tomorrow = calendar.getTime();
            today = (root, query, criteriaBuilder) -> criteriaBuilder.between(root.get("courseStartTime"),todayD,tomorrow);
        }
        //主管登录查询条件不同
        if(!isAdmin){
            user = (root, query, criteriaBuilder) -> criteriaBuilder.or(
                    criteriaBuilder.equal(root.get("user").get("id"),loginUserId),
                    criteriaBuilder.equal(root.get("coach").get("id"),loginUserId)
//                    criteriaBuilder.equal(root.get("lastUpdateLoginUser").get("id"),loginUserId)
            );
        }else {
            if (mark == 1){ //小程序
                coach = (root, query, criteriaBuilder) -> criteriaBuilder.or(
                        criteriaBuilder.equal(root.get("coach").get("id"),loginUserId)
                );
            } else {
                if(!StringUtils.isEmpty(coachId)){
                    coach = (root, query, criteriaBuilder) -> criteriaBuilder.or(
                            criteriaBuilder.equal(root.get("coach").get("id"),coachId)
//                            criteriaBuilder.equal(root.get("lastUpdateLoginUser").get("id"),coachId)
                    );
                }
            }
            if (!StringUtils.isEmpty(userId)){
                user = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("user").get("id"),userId);
            }
            if(!StringUtils.isEmpty(search)){
                userCondition = (root, query, criteriaBuilder) -> criteriaBuilder.or(
                        criteriaBuilder.like(root.get("user").get("name"),search+"%"),
                        criteriaBuilder.like(root.get("user").get("phone"),search+"%"),
                        criteriaBuilder.like(root.get("courseName"),search+"%")
                );
            }
        }
        Page<Appointment> pageResult = appointmentRepository.findAll(Specification
                .where(notDeleted)
                .and(org)
                .and(user)
                .and(coach)
                .and(history)
                .and(today)
                .and(userCondition)
                .and(contractCondition)
                .and(startDate)
                ,pageRequest);
        pageResult.stream().forEach(appointment -> {
            if(!StringUtils.isEmpty(appointment.getContractId())){
                appointment.setContract(contractRepository.findById(appointment.getContractId()).orElse(null));
            }
        });
        return pageResult;
    }


    @Transactional(rollbackFor = Throwable.class)
    public void checkUnfinishAppointment(){
       List<Appointment> list = appointmentRepository.findAllByStatus(AppointmentStatus.APPOINTMENT_SUCCESS.getStatus());
        for (Appointment appointment :list) {
            appointment.setStatus(AppointmentStatus.WAITINGFOR_FINISHCLASS.getStatus());
        }
        appointmentRepository.saveAll(list);
    }

    public Map<String, Integer> statisticsToday(String organizationId) {
        //预约数
        Integer countAppointment = appointmentRepository.countAppointment(organizationId);
        //已完成预约数(完成销课)
        Integer countFinishAppointment =  appointmentRepository.countFinishAppointment(organizationId);
        //上课教练  已上课教练
        List<Appointment> appointmentList = appointmentRepository.findFinish(organizationId);
        HashSet<String> coachSet = new HashSet<>();
        if(appointmentList != null && appointmentList.size() > 0){
            for (Appointment appointment : appointmentList) {
                coachSet.add(appointment.getCoach().getId());
            }
        }
        Integer countCoach = coachSet.size();
        //上课用户  已上课用户
        Integer countUser = appointmentRepository.countUser(organizationId);
        Map<String,Integer> resultMap = new HashMap<>();
        resultMap.put("countAppointment",countAppointment);
        resultMap.put("countFinishAppointment",countFinishAppointment);
        resultMap.put("countCoach",countCoach);
        resultMap.put("countUser",countUser);
        return resultMap;
    }

    @Transactional
    public void checkCoachClassNumber() {
        logger.info("开始执行定时任务：checkCoachClassNumber");
        //查询已核销的约课信息
        List<Appointment> appointmentList = appointmentRepository.findFinished();
        Map<String, Integer> map = new HashMap<>();
        //统计各个教练截止目前每天上课已核销数量
        for (Appointment appointment : appointmentList) {
                if(appointment.getCoach() != null){
                    String coachId = appointment.getCoach().getId();
                    String organizationId = "";
                    if(appointment.getOrganization() != null){
                        organizationId = appointment.getOrganization().getId();
                    }
                    Date courseStartDate = null;
                    if(appointment.getCourseStartDate() != null){
                        courseStartDate = appointment.getCourseStartDate();
                    }
                    String key = coachId + "," + organizationId + "," + courseStartDate;
                    if(map.containsKey(key)){
                        Integer value = map.get(key);
                        value++;
                        map.put(key,value);
                    } else {
                        map.put(key,1);
                    }
                }
        }
        for (Map.Entry<String, Integer> entry : map.entrySet()) {
            ClassHourStatistics classHourStatistics = new ClassHourStatistics();
            classHourStatistics.setCreateTime(new Date());
            String[] split = entry.getKey().split(",");
            classHourStatistics.setCoachId(split[0]);
            if(!StringUtils.isEmpty(split[1])){
                classHourStatistics.setOrganizationId(split[1]);
            }
            if (!StringUtils.isEmpty(split[2])){
                SimpleDateFormat simpleDateFormat = new SimpleDateFormat("yyyy-MM-dd");
                try {
                    Date createTime = simpleDateFormat.parse(split[2]);
                    classHourStatistics.setStatisticsDate(createTime);
                } catch (ParseException e) {
                    e.printStackTrace();
                }
            }
            classHourStatistics.setClassNumber(entry.getValue());
            //修改该教练课时数变化了的数据
            int i = classHourStatisticsRepository.insert(
                    classHourStatistics.getClassNumber(),
                    classHourStatistics.getCoachId(),
                    classHourStatistics.getStatisticsDate(),
                    new Date(), classHourStatistics.getOrganizationId());
            if (i < 1){
                classHourStatisticsRepository.update(
                        classHourStatistics.getCoachId(),
                        classHourStatistics.getOrganizationId(),
                        classHourStatistics.getStatisticsDate(),
                        classHourStatistics.getClassNumber());
            }
        }
        logger.info("执行定时任务结束：checkCoachClassNumber");
    }

    public List<Map<String,Object>> totalClassHour(Integer days, String organizationId) {
        List<Map<String,Object>> dataList = classHourStatisticsRepository.totalStatistics(days,organizationId);
        List<Map<String, Object>> list = getMaps(days, dataList);
        return list;
    }


    //查询全部
    public List<Map<String,Object>> allCoach(String organizationId, Integer days) {
        List<LoginUserAuthority> loginUserAuthorityList = loginUserAuthorityRepository.findCoach(organizationId);
        List<Map<String, Object>> resultList = new ArrayList<>();
        if(loginUserAuthorityList != null && loginUserAuthorityList.size() > 0){
            for (LoginUserAuthority loginUserAuthority : loginUserAuthorityList) {
                Map<String, Object> map = new HashMap<>();
                List<Map<String, Object>> list = singleCoach(organizationId, days, loginUserAuthority.getLoginUser().getId());
                map.put("coachName","");
                if(!StringUtils.isEmpty(loginUserAuthority.getInEntityNickname())){
                    map.put("coachName",loginUserAuthority.getInEntityNickname());
                }
                map.put("coachId",loginUserAuthority.getLoginUser().getId());
                map.put("data",list);
                resultList.add(map);
            }
        }
        return resultList;
    }

    //查询单个教练
    public List<Map<String,Object>> singleCoach(String organizationId, Integer days, String coachId) {
        List<Map<String,Object>> dataList = classHourStatisticsRepository.singleCoach(organizationId,days,coachId);
        List<Map<String, Object>> list = getMaps(days, dataList);
        return list;
    }

    private List<Map<String, Object>> getMaps(Integer days, List<Map<String, Object>> dataList) {
        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");
        List<Map<String, Object>> list = new ArrayList<>();
        Calendar c = Calendar.getInstance();
        c.setTime(new Date());
        c.add(Calendar.DATE, -(days + 1));
        for (int i = 0; i < days; i++) {
            c.add(Calendar.DATE, +1);
            Date d = c.getTime();
            String day = sdf.format(d);
            Map<String, Object> map = new HashMap<>();
            map.put("statistics_date", day);
            map.put("classHour", 0);
            list.add(map);
        }
        for (Map<String, Object> map : list) {
            String statisticsDate = (String) map.get("statistics_date");
            for (Map<String, Object> objectMap : dataList) {
                if (statisticsDate.equals(objectMap.get("statistics_date").toString())) {
                    map.put("classHour", objectMap.get("classHour"));
                }
            }
        }
        return list;
    }

    @Transactional
    public void updateClassHour(String organizationId, String coachId, Date date) {
        if(!StringUtils.isEmpty(coachId)){//只删除该教练当天数据
            classHourStatisticsRepository.deleteByCoachIdAndStatisticsDate(organizationId,coachId,date);
        } else { //删除当天所有教练数据
            classHourStatisticsRepository.deleteByDate(organizationId,date);
        }
    }

    @Transactional
    public void updateClassHourStatistics() {
        classHourStatisticsRepository.deleteAll();
        List<Appointment> appointmentList = appointmentRepository.findFinished();
        Map<String, Integer> map = new HashMap<>();
        //统计各个教练截止目前每天上课已核销数量
        for (Appointment appointment : appointmentList) {
                if(appointment.getCoach() != null){
                    String coachId = appointment.getCoach().getId();
                    String organizationId = "";
                    if(appointment.getOrganization() != null){
                        organizationId = appointment.getOrganization().getId();
                    }
                    Date courseStartDate = null;
                    if(appointment.getCourseStartDate() != null){
                        courseStartDate = appointment.getCourseStartDate();
                    }
                    String key = coachId + "," + organizationId + "," + courseStartDate;
                    if(map.containsKey(key)){
                        Integer value = map.get(key);
                        value++;
                        map.put(key,value);
                    } else {
                        map.put(key,1);
                    }
                }
        }
        for (Map.Entry<String, Integer> entry : map.entrySet()) {
            ClassHourStatistics classHourStatistics = new ClassHourStatistics();
            classHourStatistics.setCreateTime(new Date());
            String[] split = entry.getKey().split(",");
            classHourStatistics.setCoachId(split[0]);
            if(!StringUtils.isEmpty(split[1])){
                classHourStatistics.setOrganizationId(split[1]);
            }
            if (!StringUtils.isEmpty(split[2])){
                SimpleDateFormat simpleDateFormat = new SimpleDateFormat("yyyy-MM-dd");
                try {
                    Date createTime = simpleDateFormat.parse(split[2]);
                    classHourStatistics.setStatisticsDate(createTime);
                } catch (ParseException e) {
                    e.printStackTrace();
                }
            }
            classHourStatistics.setClassNumber(entry.getValue());
            //更新
            classHourStatisticsRepository.insert(
                    classHourStatistics.getClassNumber(),
                    classHourStatistics.getCoachId(),
                    classHourStatistics.getStatisticsDate(),
                    classHourStatistics.getCreateTime(),
                    classHourStatistics.getOrganizationId());
        }
    }
}

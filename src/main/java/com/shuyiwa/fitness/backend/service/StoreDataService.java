package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.StoreDataBehaviorType;
import com.shuyiwa.fitness.backend.domain.dict.StoreDataType;
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

@Service
public class StoreDataService {
    private static final Log logger = LogFactory.getLog(StoreDataService.class);

    @Autowired
    ContractRepository contractRepository;

    @Autowired
    AppointmentRepository appointmentRepository;

    @Autowired
    StoreDataRepository storeDataRepository;

    @Autowired
    UserAndCoachRepository userAndCoachRepository;

    @Autowired
    StoreDataDetailsRepository storeDataDetailsRepository;

    @Autowired
    LoginUserRepository loginUserRepository;

    @Autowired
    CourseRepository courseRepository;

    @Autowired
    RecordStoreDataRepository recordStoreDataRepository;


    @Transactional
    public void checkStoreData() throws ParseException {
        logger.info("开始执行定时任务：checkStoreData");
        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");;
        List<StoreData> storeDataList = new ArrayList<>();
        //课程购买金额
        Map<String,Object> totalAmountMap = contractRepository.countTotalAmountByYesterday();
        if (!StringUtils.isEmpty(totalAmountMap.get("createTime")) && !StringUtils.isEmpty(totalAmountMap.get("totalAmount"))){
            Date create_time1 = sdf.parse(totalAmountMap.get("createTime").toString());
            Integer totalAmount = Integer.parseInt(totalAmountMap.get("totalAmount").toString());
            StoreData storeData1 = new StoreData();
            storeData1.setCreateTime(new Date());
            storeData1.setStatisticsDate(create_time1);
            storeData1.setData(totalAmount);
            storeData1.setType(StoreDataType.totalAmount.name());
            storeDataList.add(storeData1);
        }
        //营收
        Map<String,Object> revenueAmountMap = contractRepository.countRevenueAmountByYesterday();
        if (!StringUtils.isEmpty(revenueAmountMap.get("createTime")) && !StringUtils.isEmpty(revenueAmountMap.get("revenueAmount"))) {
            Date create_time2 = sdf.parse(revenueAmountMap.get("createTime").toString());
            Integer revenueAmount = Integer.parseInt(revenueAmountMap.get("revenueAmount").toString());
            StoreData storeData2 = new StoreData();
            storeData2.setCreateTime(new Date());
            storeData2.setStatisticsDate(create_time2);
            storeData2.setData(revenueAmount);
            storeData2.setType(StoreDataType.revenueAmount.name());
            storeDataList.add(storeData2);
        }
        //新客
        Map<String,Object> newCustomerMap = contractRepository.countNewCustomerByYesterday();
        if (!StringUtils.isEmpty(newCustomerMap.get("createTime")) && !StringUtils.isEmpty(newCustomerMap.get("newCustomer"))) {
            Date create_time3 = sdf.parse(newCustomerMap.get("createTime").toString());
            Integer newCustomer = Integer.parseInt(newCustomerMap.get("newCustomer").toString());
            StoreData storeData3 = new StoreData();
            storeData3.setCreateTime(new Date());
            storeData3.setStatisticsDate(create_time3);
            storeData3.setData(newCustomer);
            storeData3.setType(StoreDataType.newCustomer.name());
            storeDataList.add(storeData3);
        }
        //完成课程数
        Map<String,Object> finishAppointmentMap = appointmentRepository.countFinishByYesterday();
        if (!StringUtils.isEmpty(finishAppointmentMap.get("confirmTime")) && !StringUtils.isEmpty(finishAppointmentMap.get("finishCount"))) {
            Date confirmTime = sdf.parse(finishAppointmentMap.get("confirmTime").toString());
            Integer finishCount = Integer.parseInt(finishAppointmentMap.get("finishCount").toString());
            StoreData storeData4 = new StoreData();
            storeData4.setCreateTime(new Date());
            storeData4.setStatisticsDate(confirmTime);
            storeData4.setData(finishCount);
            storeData4.setType(StoreDataType.finishAppointment.name());
            storeDataList.add(storeData4);
        }
        //购买课程数
        Map<String,Object> classHourMap = contractRepository.countClassHourByYesterday();
        if (!StringUtils.isEmpty(classHourMap.get("createTime")) && !StringUtils.isEmpty(classHourMap.get("classHour"))) {
            Date create_time5 = sdf.parse(classHourMap.get("createTime").toString());
            Integer classHour = Integer.parseInt(classHourMap.get("classHour").toString());
            StoreData storeData5 = new StoreData();
            storeData5.setCreateTime(new Date());
            storeData5.setStatisticsDate(create_time5);
            storeData5.setData(classHour);
            storeData5.setType(StoreDataType.classHour.name());
            storeDataList.add(storeData5);
        }
        storeDataRepository.saveAll(storeDataList);
        logger.info("执行定时任务结束：checkStoreData");
    }



    public Map<String, Object> findStoreData1(String startDateStr, String endDateStr) throws ParseException {
        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");;
        Date startDate = sdf.parse(startDateStr);
        Date endStart = sdf.parse(endDateStr);
        Map<String, Object> resultMap = new HashMap<>();
        //上课率
        Integer totalUser = userAndCoachRepository.countUserId();
        Integer classUser = appointmentRepository.countClassUser(startDate, endStart);
        Map classRate = new HashMap();
        classRate.put("totalUser", totalUser);
        classRate.put("classUser", classUser);
        resultMap.put("classRate", classRate);
        int days = (int) ((endStart.getTime() - startDate.getTime()) / (1000 * 3600 * 24)) + 1;
        //营收
        List<Map<String, Object>> revenueAmountList = storeDataRepository.findByTypeAndDate(StoreDataType.revenueAmount.name(), startDate, endStart);
        revenueAmountList = getMaps(startDate, days, revenueAmountList);
        resultMap.put("revenueAmountList", revenueAmountList);
        //新客
        List<Map<String, Object>> newCustomerList = storeDataRepository.findByTypeAndDate(StoreDataType.newCustomer.name(), startDate, endStart);
        newCustomerList = getMaps(startDate, days, newCustomerList);
        resultMap.put("newCustomerList", newCustomerList);
        //完成课程数
        List<Map<String, Object>> finishAppointmentList = storeDataRepository.findByTypeAndDate(StoreDataType.finishAppointment.name(), startDate, endStart);
        finishAppointmentList = getMaps(startDate, days, finishAppointmentList);
        resultMap.put("finishAppointmentList", finishAppointmentList);
        //购买课程
        List<Map<String, Object>> classHourList = storeDataRepository.findByTypeAndDate(StoreDataType.classHour.name(), startDate, endStart);
        classHourList = getMaps(startDate, days, classHourList);
        resultMap.put("classHourList", classHourList);
        //课程购买金额
        List<Map<String, Object>> totalAmountList = storeDataRepository.findByTypeAndDate(StoreDataType.totalAmount.name(), startDate, endStart);
        totalAmountList = getMaps(startDate, days, totalAmountList);
        resultMap.put("totalAmountList", totalAmountList);
        return resultMap;
    }

    public Map<String, Object> findClassRate(String startDateStr, String endDateStr) throws ParseException {
        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");;
        Date startDate = sdf.parse(startDateStr);
        Date endDate = sdf.parse(endDateStr);
        //上课率
        Integer totalUser = userAndCoachRepository.countUserId();
        Integer classUser = appointmentRepository.countClassUser(startDate, endDate);
        Map classRate = new HashMap();
        classRate.put("totalUser", totalUser);
        classRate.put("classUser", classUser);
        return classRate;
    }

    public List<Map<String, Object>> findStoreData(String startDateStr, String endDateStr,String type) throws ParseException {
        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");;
        Date startDate = sdf.parse(startDateStr);
        Date endDate = sdf.parse(endDateStr);
        List<Map<String, Object>> revenueAmountList = new ArrayList<>();
        int days = (int) ((endDate.getTime() - startDate.getTime()) / (1000 * 3600 * 24));
        if (days > 60){
            revenueAmountList = storeDataRepository.findByTypeAndMonth(type, startDate, endDate);
            revenueAmountList = getMaps1(startDate,endDate,revenueAmountList);
        } else {
            revenueAmountList = storeDataRepository.findByTypeAndDate(type, startDate, endDate);
            revenueAmountList = getMaps(startDate, days, revenueAmountList);
        }
        return revenueAmountList;
    }

    private List<Map<String, Object>> getMaps1(Date startDate, Date endDate, List<Map<String, Object>> dataList) {
        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM");
        List<Map<String, Object>> list = new ArrayList<>();
        Calendar calendar = Calendar.getInstance();
        while (startDate.getTime()<=endDate.getTime()){
            Map<String, Object> map = new HashMap<>();
            map.put("statistics_date", sdf.format(startDate));
            map.put("data", 0);
            list.add(map);
            calendar.setTime(startDate);
            calendar.add(Calendar.MONTH, 1);
            startDate=calendar.getTime();
        }
        for (Map<String, Object> map : list) {
            String statisticsDate = (String) map.get("statistics_date");
            for (Map<String, Object> objectMap : dataList) {
                if (statisticsDate.equals(objectMap.get("statistics_date").toString())) {
                    map.put("data", objectMap.get("data"));
                }
            }
        }
        return list;
    }

    private List<Map<String, Object>> getMaps(Date startDate, Integer days, List<Map<String, Object>> dataList) {
        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");;
        List<Map<String, Object>> list = new ArrayList<>();
        Calendar c = Calendar.getInstance();
        c.setTime(startDate);
        for (int i = 0; i < days; i++) {
            Date d = c.getTime();
            String day = sdf.format(d);
            Map<String, Object> map = new HashMap<>();
            map.put("statistics_date", day);
            map.put("data", 0);
            list.add(map);
            c.add(Calendar.DATE, +1);
        }
        for (Map<String, Object> map : list) {
            String statisticsDate = (String) map.get("statistics_date");
            for (Map<String, Object> objectMap : dataList) {
                if (statisticsDate.equals(objectMap.get("statistics_date").toString())) {
                    map.put("data", objectMap.get("data"));
                }
            }
        }
        return list;
    }

    public Page<StoreDataDetails> findStoreDataDetails(String startDateStr, String endDateStr, String coachId, String type, int page, int size) throws ParseException {
        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");;
        String[] types = type.split(",");
        Date startDate = sdf.parse(startDateStr);
        Date endDate = sdf.parse(endDateStr);
        PageRequest pageRequest = PageRequest.of(page, size, Sort.by("createTime").descending());
        Specification<StoreDataDetails> empty = Specification.where(null);
        Specification<StoreDataDetails> dateCondition = (root, query, criteriaBuilder) -> criteriaBuilder.between(root.get("createTime"), startDate, endDate);
        Specification<StoreDataDetails> typeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.and(root.get("type").in(types));
        Specification<StoreDataDetails> coachCondition = StringUtils.isEmpty(coachId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("coachIds"),"%"+coachId+"%");
        Page<StoreDataDetails> pageResult = storeDataDetailsRepository.findAll(Specification
                        .where(dateCondition)
                        .and(typeCondition)
                        .and(coachCondition)
                , pageRequest);
        pageResult.stream().forEach(storeDataDetails -> {
            if (StoreDataBehaviorType.buyCourse.name().equals(storeDataDetails.getBehavior())
                    || StoreDataBehaviorType.modifyContract.name().equals(storeDataDetails.getBehavior())
                    || StoreDataBehaviorType.refund.name().equals(storeDataDetails.getBehavior())) {
                if (!StringUtils.isEmpty(storeDataDetails.getDataId())) {
                    Contract contract = contractRepository.findById(storeDataDetails.getDataId()).orElse(null);
                    if (contract != null) {
                        String[] signatoryIds = contract.getSignatoryId().split(",");
                        String coachName = "";
                        int length = signatoryIds.length;
                        for (String signatoryId : signatoryIds) {
                            LoginUser loginUser = loginUserRepository.findById(signatoryId).orElse(null);
                            if (loginUser != null) {
                                if (length == signatoryIds.length) {
                                    coachName = loginUser.getName();
                                } else {
                                    coachName = coachName + "、" + loginUser.getName();
                                }
                                length--;
                            }
                        }
                        storeDataDetails.setProperty("coachName", coachName);
                        storeDataDetails.setProperty("userName", contract.getUser().getName());
                        storeDataDetails.setProperty("userPhone", contract.getUser().getPhone());
                        storeDataDetails.setProperty("userCreateTime", contract.getUser().getCreateTime());
                        storeDataDetails.setProperty("numberId", contract.getNumberId());
                        storeDataDetails.setProperty("courseName", courseRepository.findById(contract.getCourseId()).get().getName());
                    }
                }
            } else {
                if (!StringUtils.isEmpty(storeDataDetails.getDataId())) {
                    Appointment appointment = appointmentRepository.findById(storeDataDetails.getDataId()).orElse(null);
                    if (appointment != null) {
                        String coachName = appointment.getCoach().getName();
                        storeDataDetails.setProperty("coachName", coachName);
                        storeDataDetails.setProperty("userName", appointment.getUser().getName());
                        Contract contract = contractRepository.findById(appointment.getContractId()).orElse(null);
                        if (contract != null) {
                            storeDataDetails.setProperty("numberId", contract.getNumberId());
                            storeDataDetails.setProperty("userCreateTime", contract.getUser().getCreateTime());
                            storeDataDetails.setProperty("userPhone", contract.getUser().getPhone());

                        }
                        storeDataDetails.setProperty("courseName", appointment.getCourseName());
                    }
                }
            }
        });
        return pageResult;
    }

    @Transactional
    public void checkRecordStoreData() {
        logger.info("开始执行定时任务：checkRecordStoreData");
        List<RecordStoreData> recordStoreDataList = recordStoreDataRepository.findByDeleted(false);
        List<StoreData> storeDataList = new ArrayList<>();
        if (recordStoreDataList!= null && recordStoreDataList.size() > 0){
            for (RecordStoreData recordStoreData : recordStoreDataList) {
                Contract contract = contractRepository.findById(recordStoreData.getContractId()).orElse(null);
                if (contract != null){
                    if (recordStoreData.getField() == 1 || recordStoreData.getField() == 3) {
                        Integer refundAmount = contractRepository.countRefundAmountByCreateTime(contract.getCreateTime());
                        Integer totalAmount = contractRepository.countTotalAmountByCreateTime(contract.getCreateTime());
                        StoreData storeData = new StoreData();
                        storeData.setCreateTime(new Date());
                        storeData.setType(StoreDataType.totalAmount.name());
                        storeData.setStatisticsDate(contract.getCreateTime());
                        storeData.setData(totalAmount);
                        storeDataRepository.deleteByTypeAndStatisticsDate(StoreDataType.totalAmount.name(),contract.getCreateTime());
                        storeDataList.add(storeData);
                        Integer revenueAmount = totalAmount - refundAmount;
                        StoreData storeData2 = new StoreData();
                        storeData2.setCreateTime(new Date());
                        storeData2.setStatisticsDate(contract.getCreateTime());
                        storeData2.setData(revenueAmount);
                        storeData2.setType(StoreDataType.revenueAmount.name());
                        storeDataRepository.deleteByTypeAndStatisticsDate(StoreDataType.revenueAmount.name(),contract.getCreateTime());
                        storeDataList.add(storeData2);
                    } else if (recordStoreData.getField() == 2){
                        Integer classHour = contractRepository.countClassHourByCreateTime(contract.getCreateTime());
                        StoreData storeData3 = new StoreData();
                        storeData3.setCreateTime(new Date());
                        storeData3.setStatisticsDate(contract.getCreateTime());
                        storeData3.setData(classHour);
                        storeData3.setType(StoreDataType.classHour.name());
                        storeDataRepository.deleteByTypeAndStatisticsDate(StoreDataType.classHour.name(),contract.getCreateTime());
                        storeDataList.add(storeData3);
                    }
                }
                recordStoreData.setDeleted(true);
            }
        }
        storeDataRepository.saveAll(storeDataList);
        logger.info("执行定时任务结束：checkRecordStoreData");
    }

    public Integer totalRevenue(String startDateStr, String endDateStr, String coachId, String type) throws ParseException {
        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");
        String[] types = type.split(",");
        Date startDate = sdf.parse(startDateStr);
        Date endDate = sdf.parse(endDateStr);
        Integer totalRevenue = storeDataDetailsRepository.totalRevenue(startDate,endDate,coachId,types);
        return totalRevenue;
    }
}
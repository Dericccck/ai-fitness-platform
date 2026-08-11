package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.ContractStatus;
import com.shuyiwa.fitness.backend.domain.dict.StoreDataBehaviorType;
import com.shuyiwa.fitness.backend.domain.dict.StoreDataDetailsStatus;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.text.DateFormat;
import java.util.*;

@Service
public class ContractService {

    @Autowired
    private LoginUserRepository loginUserRepository;

    @Autowired
    private ContractRepository contractRepository;

    @Autowired
    private CourseRepository courseRepository;

    @Autowired
    private AppointmentRepository appointmentRepository;

    @Autowired
    private UserAndCoachRepository userAndCoachRepository;

    @Autowired
    private UserCoachHistoryService userCoachHistoryService;

    @Autowired
    private ContractHistoryService contractHistoryService;

    @Autowired
    private StoreDataDetailsRepository storeDataDetailsRepository;

    @Autowired
    RecordStoreDataRepository recordStoreDataRepository;

    @Transactional
    public void saveContract(FrogUserDetails frogUserDetails, Contract contract) {
        if (StringUtils.isEmpty(contract.getFinishClassHour())) {
            contract.setFinishClassHour(0);
        }
        if (contract.getClassHour() < contract.getFinishClassHour()) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "核销课时数有误");
        }
        LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        UserAndCoach userAndCoach = userAndCoachRepository.findByOrganizationAndUser(contract.getOrganization(), contract.getUser()).orElse(null);
        if (!StringUtils.isEmpty(userAndCoach)) {
            if (StringUtils.isEmpty(userAndCoach.getHeadCoachIds())) {
                userAndCoach.setHeadCoachIds(loginUser.getId());
                //保存换教练记录
                userCoachHistoryService.save(contract.getUser().getId(), loginUser.getId(), contract.getOrganization().getId(),loginUser.getId());
            }
        } else {
            userAndCoach = new UserAndCoach();
            userAndCoach.setUser(contract.getUser());
            userAndCoach.setCoach(loginUser);
            userAndCoach.setHeadCoachIds(loginUser.getId());
            userAndCoach.setOrganization(contract.getOrganization());
            userAndCoach.setCreateLoginUser(loginUser);
            userAndCoach.setStatus(1);
            userAndCoach.setVersion(0);
            userAndCoach.setDeleted(false);
            userAndCoach.setClassHour(contract.getClassHour());
            userAndCoach.setUserStatus(1);
            userAndCoach.setCreateTime(new Date());
            userAndCoachRepository.save(userAndCoach);

            //保存换教练记录
            userCoachHistoryService.save(contract.getUser().getId(), loginUser.getId(), contract.getOrganization().getId(), loginUser.getId());
        }
        StoreDataDetails storeDataDetails = new StoreDataDetails();
        storeDataDetails.setType(StoreDataDetailsStatus.OTHER.getStatus());
        Integer count = contractRepository.countByUserId(contract.getUser().getId(),contract.getOrganization().getId());
        if (count < 1){
            contract.setNewCustomer(true);
            storeDataDetails.setType(StoreDataDetailsStatus.NEW_CUSTOMER.getStatus());
        }
        contract.setCreator(frogUserDetails.getUsername());
        contract.setRemainingClassHours(contract.getClassHour() - contract.getFinishClassHour());
        contract.setStatus(ContractStatus.Contract_NORMAL.getStatus());
        contract.setDeleted(0);
        //保存
        Contract contract1 = contractRepository.save(contract);

        storeDataDetails.setDataId(contract1.getId());
        storeDataDetails.setBehavior(StoreDataBehaviorType.buyCourse.name());
        storeDataDetails.setExecNum(contract1.getClassHour());
        storeDataDetails.setExecAmount(contract1.getTotalAmount());
        storeDataDetails.setRevenueAmount(contract1.getTotalAmount());
        storeDataDetails.setCoachIds(contract1.getSignatoryId());
        //保存店铺数据详情
        storeDataDetailsRepository.save(storeDataDetails);
    }

    /**
     * 根据合约id查询约课信息
     *
     * @param contractId
     * @return
     */
    public Contract findAppointmentByContractId(String contractId, int page, int size) {
        PageRequest pageRequest = PageRequest.of(page, size, Sort.by("createTime").descending());
        Contract contract = findContractById(contractId);
        Specification<Appointment> empty1 = Specification.where(null);
        Specification<Appointment> contractIdCondition = StringUtils.isEmpty(contract.getId()) ? empty1 : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contractId"), contract.getId());
        Page<Appointment> appointmentPage = appointmentRepository.findAll(Specification.where(contractIdCondition), pageRequest);
        for (Appointment appointment : appointmentPage.getContent()) {
            String headCoachName = "";
            if (!StringUtils.isEmpty(appointment.getHeadCoachIds())){
                String[] headCoachIds = appointment.getHeadCoachIds().split(",");
                int i = 0;
                for (String headCoachId : headCoachIds) {
                    LoginUser coach = loginUserRepository.findById(headCoachId).orElse(null);
                    if (coach != null){
                        if (i == 0) {
                            headCoachName = headCoachName + coach.getName();
                            i++;
                        } else {
                            headCoachName = headCoachName + "、" + coach.getName();
                        }
                    }
                }
            }
            appointment.setProperty("headCoachName", headCoachName);
            if (!StringUtils.isEmpty(appointment.getCoach())) {
                    appointment.setProperty("coachName", appointment.getCoach().getName());

            }
        }
        if (StringUtils.isEmpty(contract.getFinishClassHour())) {
            contract.setFinishClassHour(0);
        }
        contract.setProperty("appointmentPage", appointmentPage);
        return contract;
    }

    public Contract findById(String contractId) {
        Contract contract = contractRepository.findById(contractId).orElse(null);
        return contract;
    }

    /**
     * 查询单个合同信息
     *
     * @param contractId
     * @return
     */
    public Contract findContractById(String contractId) {
        Contract contract = findById(contractId);
        List<Map<String, String>> list = new ArrayList<>();
        if (!StringUtils.isEmpty(contract.getSignatoryId())) {
            String[] signatoryIds = contract.getSignatoryId().split(",");
            for (String signatoryId : signatoryIds) {
                LoginUser loginUser = loginUserRepository.findById(signatoryId).orElse(null);
                if (!StringUtils.isEmpty(loginUser)) {
                    Map<String, String> map = new HashMap<>();
                    map.put("signatoryId", signatoryId);
                    map.put("signatoryName", loginUser.getName());
                    list.add(map);
                }
            }
        }
        if (StringUtils.isEmpty(contract.getFinishClassHour())) {
            contract.setFinishClassHour(0);
        }
        Course course = courseRepository.findById(contract.getCourseId()).orElse(null);
        contract.setProperty("course", course);
        contract.setProperty("signatory", list);
        return contract;
    }

    /**
     * 查询有效合约且开启的课程
     *
     * @param userId
     * @param organizationId
     * @return
     */
    public List<Contract> findValidContract(String userId, String organizationId) {
        //查询有效的合同
        List<Contract> contractList = contractRepository.findValidContract(userId, organizationId, ContractStatus.Contract_NORMAL.getStatus());
        if (contractList != null && contractList.size() > 0) {
            for (Contract contract : contractList) {
                if (StringUtils.isEmpty(contract.getFinishClassHour())) {
                    contract.setFinishClassHour(0);
                }
                //查询开启的课程
                Course course = courseRepository.findById(contract.getCourseId()).orElse(null);
                if (!StringUtils.isEmpty(course) && course.getStatus().equals(1)) {
                    contract.setCourseName(course.getName());
                }
            }
        }
        return contractList;
    }

    public Page<Contract> pageContract(String organizationId, String search, String status, int page, int size, String type) {
        PageRequest pageRequest = PageRequest.of(page, size, Sort.by("contractEndTime").descending());
        Specification<Contract> empty = Specification.where(null);
        Specification<Contract> organizationCondition = StringUtils.isEmpty(organizationId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), organizationId);
        Specification<Contract> statusCondition = empty;
        statusCondition = StringUtils.isEmpty(status) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.and(root.get("status").in(status.split(",")));
        Specification<Contract> deletedCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), 0);
//        if(effectiveContract){//生效合同
//            statusCondition = (root, query, criteriaBuilder) -> criteriaBuilder.or(
//                    criteriaBuilder.lessThanOrEqualTo(root.<Date>get("contractEndTime"),new Date())
//            );
//        } else if (endContract) {//已结束合同
//            statusCondition = (root, query,1 criteriaBuilder) -> criteriaBuilder.or(
//                    criteriaBuilder.greaterThanOrEqualTo(root.<Date>get("contractEndTime"),new Date())
//            );
//        }
        Specification<Contract> userCondition = empty;
        if (!StringUtils.isEmpty(search)) {
            switch (type){
                case "1" ://手机号
                    userCondition = (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("user").get("phone"), search + "%");
                    break;
                case "2" ://客户
                    userCondition = (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("user").get("name"), search + "%");
                    break;
                case "3" ://签约者
                    List<String> loginUserIds = loginUserRepository.findBySignatoryName(search);
                    if(loginUserIds != null && loginUserIds.size() > 0){
                        for (String loginUserId : loginUserIds) {
                            userCondition = userCondition.or((root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("signatoryId"),"%" + loginUserId + "%"));
                        }
                    } else {
                        userCondition = userCondition.or((root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("signatoryId"),""));
                    }
                    break;
                case "4" ://课程
                    List<String> courseIds = courseRepository.findByName(search);
                    if(courseIds != null && courseIds.size() > 0){
                        userCondition = (root, query, criteriaBuilder) -> criteriaBuilder.and(root.get("courseId").in(courseIds));
                    } else {
                        userCondition = (root, query, criteriaBuilder) -> criteriaBuilder.and(root.get("courseId").in(""));
                    }
                    break;
                default:
                    userCondition = (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("user").get("phone"), search + "%");
            }
        }
//        Page<Contract> pageResult = null;
//        if(userCondition != empty) {
        Page<Contract> pageResult = contractRepository.findAll(Specification
                            .where(organizationCondition)
                            .and(statusCondition)
                            .and(userCondition)
                            .and(deletedCondition)
                    , pageRequest);
            pageResult.stream().forEach(contract -> {
                if (StringUtils.isEmpty(contract.getFinishClassHour())){
                    contract.setFinishClassHour(0);
                }
                Course course = courseRepository.findById(contract.getCourseId()).orElse(null);
                if (!StringUtils.isEmpty(course)) {
                    contract.setCourseName(course.getName());
                    contract.setProperty("coursePrice", course.getCoursePrice());
                }
                LoginUser loginUser = loginUserRepository.findById(contract.getUser().getId()).orElse(null);
                if (!StringUtils.isEmpty(loginUser)) {
                    contract.setProperty("userPhone", loginUser.getPhone());
                    contract.setProperty("username", loginUser.getName());
                    contract.setProperty("userCreateTime", loginUser.getCreateTime());
                }
                String signatoryName = "";
                if (!StringUtils.isEmpty(contract.getSignatoryId())) {
                    String[] signatoryIds = contract.getSignatoryId().split(",");
                    for (String signatoryId : signatoryIds) {
                        LoginUser signatory = loginUserRepository.findById(signatoryId).orElse(null);
                        if (!StringUtils.isEmpty(signatory)) {
                            signatoryName = signatoryName + signatory.getName() + " ";
                        }
                    }
                }
                contract.setProperty("signatoryName", signatoryName);
                List<UserAndCoach> userAndCoachList = userAndCoachRepository.findByUser(contract.getUser().getId());
                if (userAndCoachList.size() > 0) {
                    contract.setProperty("remarkUsername", userAndCoachList.get(0).getRemarkUserName());
                }

            });
//        }
        return pageResult;

    }

    public void contractStatus() {
        //异常结束：有余课、到期
        contractRepository.contractAbnormalEnd(ContractStatus.Contract_ABNORMALEND.getStatus());
        //正常结束：无余课、未到期或已到期
        contractRepository.contractNormalEnd(ContractStatus.Contract_NORMALEND.getStatus());
    }


    public List<Contract> findContractByUserId(String organizationId, String userId, Integer status) {
        Specification<Contract> empty = Specification.where(null);
        Specification<Contract> organizationIdCondition = StringUtils.isEmpty(organizationId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), organizationId);
        Specification<Contract> userIdCondition = StringUtils.isEmpty(userId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("user").get("id"), userId);
        Specification<Contract> statusCondition = empty;
        statusCondition = StringUtils.isEmpty(status) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("status"), status);
        Specification<Contract> deletedCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), 0);
        List<Contract> contractList = contractRepository.findAll(Specification.where(organizationIdCondition).and(userIdCondition).and(statusCondition).and(deletedCondition));
        contractList.stream().forEach(contract -> {
            if (StringUtils.isEmpty(contract.getFinishClassHour())) {
                contract.setFinishClassHour(0);
            }
            Course course = courseRepository.findById(contract.getCourseId()).orElse(null);
            if (!StringUtils.isEmpty(course)) {
                contract.setCourseName(course.getName());
                contract.setProperty("coursePrice", course.getCoursePrice());
            }
            LoginUser loginUser = loginUserRepository.findById(contract.getUser().getId()).orElse(null);
            if (!StringUtils.isEmpty(loginUser)) {
                contract.setProperty("userPhone", loginUser.getPhone());
                contract.setProperty("username", loginUser.getName());
                contract.setProperty("userCreateTime", loginUser.getCreateTime());
            }
            String signatoryName = "";
            if (!StringUtils.isEmpty(contract.getSignatoryId())) {
                String[] signatoryIds = contract.getSignatoryId().split(",");
                for (String signatoryId : signatoryIds) {
                    LoginUser signatory = loginUserRepository.findById(signatoryId).orElse(null);
                    if (!StringUtils.isEmpty(signatory)) {
                        signatoryName = signatoryName + signatory.getName() + " ";
                    }
                }
            }
            contract.setProperty("signatoryName", signatoryName);
            List<UserAndCoach> userAndCoachList = userAndCoachRepository.findByUser(contract.getUser().getId());
            if (userAndCoachList.size() > 0) {
                contract.setProperty("remarkUsername", userAndCoachList.get(0).getRemarkUserName());
            }

            List<Appointment> appointmentList = appointmentRepository.findAllByContractId(contract.getId(), contract.getOrganization().getId());
            if (appointmentList != null && appointmentList.size() > 0) {
                contract.setProperty("LastCourseStartTime", appointmentList.get(0).getCourseStartTime());
            } else {
                contract.setProperty("LastCourseStartTime", null);
            }
        });
        return contractList;
    }

    public void updateContract(Contract contract, Contract contractDB, FrogUserDetails frogUserDetails) throws FrogException{
        contract.setRemainingClassHours(contract.getClassHour() - contract.getFinishClassHour());
        contract.setCreator(frogUserDetails.getUsername());
        Map<String, Object> afterMap = new HashMap<>();
        Integer num = contractRepository.updateContract(contract.getCourseId(), contract.getNumberId(), contract.getContractEndTime(), contract.getTotalAmount(), contract.getClassHour(), contract.getSignatoryId(), contract.getId(), contract.getRemainingClassHours(), contract.getCreator(), contract.getFinishClassHour(),contractDB.getVersion());
        if(num==0){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"操作错误，请重试");
        }
        StoreDataDetails storeDataDetails = new StoreDataDetails();
        Boolean flag = false;
        if (!contract.getCourseId().equals(contractDB.getCourseId())) {
            afterMap.put("courseId", contract.getCourseId());
        }
        if (!contract.getNumberId().equals(contractDB.getNumberId())) {
            afterMap.put("numberId", contract.getNumberId());
        }
        DateFormat dateFormat = DateFormat.getDateInstance();
        String contractEndTime = dateFormat.format(contract.getContractEndTime());
        String contractEndTimeDB = dateFormat.format(contractDB.getContractEndTime());
        if (!contractEndTime.equals(contractEndTimeDB)) {
            afterMap.put("contractEndTime", contractEndTime);
        }
        if (!contract.getTotalAmount().equals(contractDB.getTotalAmount())) {
            afterMap.put("totalAmount", contract.getTotalAmount());
            RecordStoreData recordStoreData = new RecordStoreData();
            recordStoreData.setField(1);
            recordStoreData.setContractId(contractDB.getId());
            recordStoreData.setDeleted(false);
            recordStoreData.setCreateTime(new Date());
            recordStoreDataRepository.save(recordStoreData);
            storeDataDetails.setExecAmount(contract.getTotalAmount() - contractDB.getTotalAmount());
            storeDataDetails.setRevenueAmount(contract.getTotalAmount() - contractDB.getTotalAmount());
            flag = true;
        }
        if (!contract.getClassHour().equals(contractDB.getClassHour())) {
            afterMap.put("classHour",contract.getClassHour());
            RecordStoreData recordStoreData = new RecordStoreData();
            recordStoreData.setField(2);
            recordStoreData.setContractId(contractDB.getId());
            recordStoreData.setDeleted(false);
            recordStoreData.setCreateTime(new Date());
            recordStoreDataRepository.save(recordStoreData);
            storeDataDetails.setExecNum(contract.getClassHour() - contractDB.getClassHour());
            flag = true;
        }
        if (!contract.getSignatoryId().equals(contractDB.getSignatoryId())) {
            afterMap.put("signatoryId", contract.getSignatoryId());
        }
        if (!contract.getFinishClassHour().equals(contractDB.getFinishClassHour())) {
            afterMap.put("finishClassHour", contract.getFinishClassHour());
        }
        if (!afterMap.isEmpty()){
            contractHistoryService.save(contractDB, afterMap,frogUserDetails.getLoginUser(loginUserRepository));
        }
        if (flag){
            storeDataDetails.setType(StoreDataDetailsStatus.MODIFY_CONTRACT.getStatus());
            storeDataDetails.setDataId(contractDB.getId());
            storeDataDetails.setBehavior(StoreDataBehaviorType.modifyContract.name());
            storeDataDetails.setCoachIds(contract.getSignatoryId());
            //保存店铺数据详情
            storeDataDetailsRepository.save(storeDataDetails);
        }
    }


    public Page<Contract> pageContract(String organizationId, String search, String status, int page, int size) {
        PageRequest pageRequest = PageRequest.of(page, size, Sort.by("contractEndTime").descending());
        Specification<Contract> empty = Specification.where(null);
        Specification<Contract> organizationCondition = StringUtils.isEmpty(organizationId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), organizationId);
        Specification<Contract> statusCondition = empty;
        statusCondition = StringUtils.isEmpty(status) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.and(root.get("status").in(status.split(",")));
        Specification<Contract> userCondition = empty;
        Specification<Contract> numberIdCondition = StringUtils.isEmpty(search) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("numberId"), search + "%");
        Specification<Contract> nameCondition = StringUtils.isEmpty(search) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("user").get("name"), search + "%");
        Specification<Contract> deletedCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), 0);
        List<String> loginUserIds = loginUserRepository.findBySignatoryName(search);
        if (loginUserIds != null && loginUserIds.size() > 0) {
            for (String loginUserId : loginUserIds) {
                userCondition = userCondition.or((root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("signatoryId"), "%" + loginUserId + "%"));
            }
        } else {
            userCondition = userCondition.or((root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("signatoryId"), ""));
        }
        Page<Contract> pageResult = contractRepository.findAll(Specification
                        .where(organizationCondition)
                        .and(statusCondition)
                        .and(deletedCondition)
                        .and(userCondition.or(nameCondition).or(numberIdCondition))
                , pageRequest);
        pageResult.stream().forEach(contract -> {
            if (StringUtils.isEmpty(contract.getFinishClassHour())) {
                contract.setFinishClassHour(0);
            }
            Course course = courseRepository.findById(contract.getCourseId()).orElse(null);
            if (!StringUtils.isEmpty(course)) {
                contract.setCourseName(course.getName());
                contract.setProperty("coursePrice", course.getCoursePrice());
            }
            LoginUser loginUser = loginUserRepository.findById(contract.getUser().getId()).orElse(null);
            if (!StringUtils.isEmpty(loginUser)) {
                contract.setProperty("userPhone", loginUser.getPhone());
                contract.setProperty("username", loginUser.getName());
                contract.setProperty("userCreateTime", loginUser.getCreateTime());
            }
            String signatoryName = "";
            if (!StringUtils.isEmpty(contract.getSignatoryId())) {
                String[] signatoryIds = contract.getSignatoryId().split(",");
                int length = signatoryIds.length;
                for (String signatoryId : signatoryIds) {
                    LoginUser signatory = loginUserRepository.findById(signatoryId).orElse(null);
                    if (!StringUtils.isEmpty(signatory)) {
                        length--;
                        if (length > 0){
                            signatoryName = signatoryName + signatory.getName() + "、";
                        } else {
                            signatoryName = signatoryName + signatory.getName();
                        }
                    }
                }
            }
            contract.setProperty("signatoryName", signatoryName);
            List<UserAndCoach> userAndCoachList = userAndCoachRepository.findByUser(contract.getUser().getId());
            if (userAndCoachList.size() > 0) {
                contract.setProperty("remarkUsername", userAndCoachList.get(0).getRemarkUserName());
            }

        });
        return pageResult;
    }

}

package com.shuyiwa.fitness.backend.service;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.domain.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.text.DateFormat;
import java.text.SimpleDateFormat;
import java.util.*;

@Service
public class ContractHistoryService {

    @Autowired
    private ContractHistoryRepository contractHistoryRepository;

    @Autowired
    private LoginUserRepository loginUserRepository;

    @Autowired
    private CourseRepository courseRepository;

    public void save(Contract contract, Map<String, Object> updateMap, LoginUser loginUser) {
        String beforeData = JSONObject.toJSONString(contract);
        String updateData = JSONObject.toJSONString(updateMap);
        ContractHistory contractHistory = new ContractHistory();
        contractHistory.setContractId(contract.getId());
        contractHistory.setCreateTime(new Date());
        contractHistory.setUpdateData(updateData);
        contractHistory.setBeforeData(beforeData);
        contractHistory.setOrganizationId(contract.getOrganization().getId());
        contractHistory.setUpdateLoginUser(loginUser);
        contractHistoryRepository.save(contractHistory);
    }

    public List<ContractHistory> findByContractId(String contractId) {
        List<ContractHistory> contractHistoryList =  contractHistoryRepository.findByContractId(contractId);
        List<ContractHistory> contractHistoryListResult = new ArrayList<>();
        if (contractHistoryList != null && contractHistoryList.size() > 0){
            for (ContractHistory contractHistory : contractHistoryList) {
                JSONObject updateData = JSON.parseObject(contractHistory.getUpdateData());
                JSONObject beforeData = JSON.parseObject(contractHistory.getBeforeData());
                Set<String> keySet = updateData.keySet();
                if (keySet.contains("courseId")){
                    String afterDataString = updateData.getString("courseId");
                    String beforeDataString = beforeData.getString("courseId");
                    Course beforeCourse = courseRepository.findById(beforeDataString).orElse(null);
                    Course afterCourse = courseRepository.findById(afterDataString).orElse(null);
                    if (beforeCourse != null && afterCourse != null) {
                        String beforeCourseName = beforeCourse.getName();
                        beforeData.put("courseName", beforeCourseName);
                        String afterCourseName = afterCourse.getName();
                        updateData.put("courseName", afterCourseName);
                    }
                    keySet.remove("courseId");
                }
                if (keySet.contains("signatoryId")){
                    String afterDataString = updateData.getString("signatoryId");
                    String beforeDataString = beforeData.getString("signatoryId");
                    String[] afterSplit = afterDataString.split(",");
                    String afterSignatoryName = "";
                    int length1 = afterSplit.length;
                    for (String id : afterSplit) {
                        LoginUser loginUser = loginUserRepository.findById(id).orElse(null);
                        if (loginUser != null){
                            length1--;
                            if (length1 > 0){
                                afterSignatoryName = afterSignatoryName + loginUser.getName() + "、";
                            } else {
                                afterSignatoryName = afterSignatoryName + loginUser.getName();
                            }
                        }
                    }
                    updateData.put("signatoryName",afterSignatoryName);
                    String[] beforeSplit = beforeDataString.split(",");
                    String beforeSignatoryName = "";
                    int length = beforeSplit.length;
                    for (String id : beforeSplit) {
                        LoginUser loginUser = loginUserRepository.findById(id).orElse(null);
                        if (loginUser != null){
                            length--;
                            if (length > 0){
                                beforeSignatoryName = beforeSignatoryName + loginUser.getName() + "、";
                            } else {
                                beforeSignatoryName = beforeSignatoryName + loginUser.getName();
                            }
                        }
                    }
                    beforeData.put("signatoryName",beforeSignatoryName);
                    keySet.remove("signatoryId");
                }
                ContractHistory contractHistory1 = new ContractHistory();
                contractHistory1.setContractId(contractHistory.getContractId());
                contractHistory1.setOrganizationId(contractHistory.getOrganizationId());
                contractHistory1.setCreateTime(contractHistory.getCreateTime());
                contractHistory1.setId(contractHistory.getId());
                contractHistory1.setUpdateLoginUser(contractHistory.getUpdateLoginUser());
                contractHistory1.setUpdateData(updateData.toJSONString());
                contractHistory1.setBeforeData(beforeData.toJSONString());
                contractHistory1.setProperty("updateLoginUserName",contractHistory.getUpdateLoginUser().getName());
                contractHistoryListResult.add(contractHistory1);
            }
        }
        return contractHistoryListResult;
    }
}

package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.util.Date;
import java.util.List;
import java.util.Optional;

@Service
public class ClassHourRecordService {
    private static final Log logger = LogFactory.getLog(ClassHourRecordService.class);

    @Autowired
    ClassHourRecordRepository classHourRecordRepository;

    @Autowired
    UserAndCoachRepository userAndCoachRepository;

    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;
    



    @Transactional(rollbackFor = Throwable.class)
    public void createRecord(ClassHourRecord record) throws FrogException {
        UserAndCoach userAndCoach = userAndCoachRepository.findByOrganizationAndUser(record.getOrganization(),record.getLoginUser()).orElse(null);
        int rows = userAndCoachRepository.addAmount(record.getOrganization().getId(),record.getLoginUser().getId(),record.getAmount(),userAndCoach.getVersion());
        if(rows==0){
            throw  new FrogException(FrogException.INTERNAL_SERVER_ERROR,"添加记录异常");
        }
        Integer times = classHourRecordRepository.getMaxTimesByOrganizationAndLoginUser(record.getOrganization().getId(),record.getLoginUser().getId());
        if(times==null)times=0;
        record.setTimes(++times);
        classHourRecordRepository.save(record);

    }



    public Page<ClassHourRecord> findRecordByPage(int page,int size,String loginUserId,String orgId,String search){
        PageRequest pageRequest = PageRequest.of(page, size, Sort.by("createTime").descending());

        Specification<ClassHourRecord> empty = Specification.where(null);
        Specification<ClassHourRecord> userCondition = StringUtils.isEmpty(loginUserId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("loginUser").get("id"), loginUserId);
        Specification<ClassHourRecord> organizationCondition = StringUtils.isEmpty(orgId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), orgId);
        Specification<ClassHourRecord> searchCondition = empty;
        if(!StringUtils.isEmpty(search)){
           searchCondition =  (root, query, criteriaBuilder) -> criteriaBuilder.or(
                   criteriaBuilder.equal(root.get("loginUser").get("name"), search),
                   criteriaBuilder.equal(root.get("loginUser").get("phone"), search)
           );
        }

        Page<ClassHourRecord> pageResult = classHourRecordRepository.findAll(Specification
                        .where(userCondition)
                        .and(searchCondition)
                        .and(organizationCondition)
                , pageRequest);
        pageResult.stream().forEach(classHourRecord -> {
            List<String> list = loginUserAuthorityRepository.getNickName(classHourRecord.getOrganization().getId(),classHourRecord.getCoach().getId());
            String coachName = classHourRecord.getCoach().getName();
            if(null!=list && list.size()>0){
                coachName = list.get(0);
            }
            classHourRecord.getProperties().put("coachName",coachName);
            classHourRecord.getProperties().put("userName",classHourRecord.getLoginUser().getName());
        });
        return pageResult;

    }



}

package com.shuyiwa.fitness.backend.domain;


import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

import javax.transaction.Transactional;
import java.util.Date;
import java.util.List;
import java.util.Map;

public interface ContractRepository extends PagingAndSortingRepository<Contract, String>, JpaSpecificationExecutor<Contract> {

    @Modifying
    @Query(value = "update contract set contract_end_time=:contractEndTime, update_time=:updateTime, signatory_id=:signatoryId, status=:status, remaining_class_hours=:remainingClassHours, finish_class_hour=:finishClassHour,version=:version+1 where id = :id and version=:version ", nativeQuery = true)
    Integer updateContract2(String id, Date contractEndTime, Date updateTime, String signatoryId, Integer status, Integer remainingClassHours, Integer finishClassHour,Long version);

    @Modifying
    @Query(value = "update contract set  status = :status, number_id = :numberId, refund_amount = :refundAmount where id = :contractId", nativeQuery = true)
    void closeContract(@Param("contractId")String contractId, @Param("numberId")String numberId, @Param("status")Integer status, @Param("refundAmount")Integer refundAmount);

    @Query(value = "select * from contract where user_id=:userId and organization_id=:organizationId and status=:status and deleted=0 order by contract_create_time DESC", nativeQuery = true)
    List<Contract> findByUserId(String userId, String organizationId, Integer status);

    @Query(value = "select * from contract where number_id = :numberId and deleted=0", nativeQuery = true)
    Contract findByNumberId(String numberId);

    @Query(value = "select count(id) from contract where user_id=:userId and organization_id=:organizationId and deleted=0", nativeQuery = true)
    Integer totalContractCount(String userId, String organizationId);

    @Query(value = "select count(id) from contract where user_id=:userId and organization_id=:organizationId and status=:status and deleted=0", nativeQuery = true)
    Integer currentContractCount(String userId, String organizationId, Integer status);

    @Transactional
    @Query(value = "update contract set status = :status where status = 1 and contract_end_time <= DATE_ADD(current_date(), INTERVAL 1 day) and remaining_class_hours > 0", nativeQuery = true)
    @Modifying
    void contractAbnormalEnd(int status);

    @Transactional
    @Query(value = "update contract set status = :status where status = 1 and remaining_class_hours = 0 ", nativeQuery = true)
    @Modifying
    void contractNormalEnd(int status);

    @Query(value = "select * from contract where user_id=:userId and organization_id=:organizationId and status=:status and deleted=0 order by contract_create_time DESC", nativeQuery = true)
    List<Contract> findValidContract(String userId, String organizationId, int status);

    @Query(" from Contract where find_in_set(:signId,signatoryId)>0 and deleted=0")
    List<Contract> findBySignId(@Param("signId")String signId);

    @Query(value = "select *  from contract where find_in_set(:signId,signatory_id) and deleted=0",nativeQuery = true)
    List<Contract> findBySignIdSql(@Param("signId")String signId);


    @Transactional
    @Query(value = "update contract set course_id=:courseId, number_id = :numberId,contract_end_time = :contractEndTime," +
            "total_amount = :totalAmount,class_hour = :classHour,signatory_id = :signatoryId," +
            "remaining_class_hours=:remainingClassHours,creator=:creator,finish_class_hour=:finalClassHour,version=:version+1 where id=:id and version=:version", nativeQuery = true)
    @Modifying
    Integer updateContract(String courseId, String numberId, Date contractEndTime, Integer totalAmount, Integer classHour, String signatoryId, String id, Integer remainingClassHours, String creator, Integer finalClassHour,Long version);

    @Query(value = "select count(*) from contract where number_id = :numberId", nativeQuery = true)
    Integer countByNumberId(String numberId);

    @Query(value = "select count(*) from contract where user_id=:userId and organization_id=:organizationId", nativeQuery = true)
    Integer countByUserId(String userId, String organizationId);

    @Query(value = "select DATE_FORMAT(create_time,'%Y-%m-%d') createTime, SUM(total_amount) totalAmount from contract where TO_DAYS(NOW( )) - TO_DAYS(create_time) = 1 and deleted = 0", nativeQuery = true)
    Map<String,Object> countTotalAmountByYesterday();

    @Query(value = "select DATE_FORMAT(create_time,'%Y-%m-%d') createTime, sum(total_amount - refund_amount ) revenueAmount from contract where TO_DAYS(NOW( )) - TO_DAYS(create_time) = 1 and deleted = 0", nativeQuery = true)
    Map<String,Object> countRevenueAmountByYesterday();

    @Query(value = "select DATE_FORMAT(create_time,'%Y-%m-%d') createTime, sum(new_customer) newCustomer from contract where new_customer=true and TO_DAYS(NOW( )) - TO_DAYS(create_time) = 1 and deleted = 0", nativeQuery = true)
    Map<String,Object> countNewCustomerByYesterday();

    @Query(value = "select DATE_FORMAT(create_time,'%Y-%m-%d') createTime, sum(class_hour) classHour from contract where TO_DAYS(NOW( )) - TO_DAYS(create_time) = 1 and deleted = 0", nativeQuery = true)
    Map<String,Object> countClassHourByYesterday();

    @Query(value = "select SUM(refund_amount) from contract where DATE_FORMAT(create_time,'%Y-%m-%d') = DATE_FORMAT(:createTime,'%Y-%m-%d') and deleted = 0", nativeQuery = true)
    Integer countRefundAmountByCreateTime(@Param("createTime") Date createTime);

    @Query(value = "select SUM(total_amount) from contract where DATE_FORMAT(create_time,'%Y-%m-%d') = DATE_FORMAT(:createTime,'%Y-%m-%d') and deleted = 0", nativeQuery = true)
    Integer countTotalAmountByCreateTime(@Param("createTime")Date createTime);

    @Query(value = "select sum(class_hour) from contract where DATE_FORMAT(create_time,'%Y-%m-%d') = DATE_FORMAT(:createTime,'%Y-%m-%d') and deleted = 0", nativeQuery = true)
    Integer countClassHourByCreateTime(@Param("createTime")Date createTime);
}

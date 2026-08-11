package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.Date;
import java.util.List;

public interface SystemMessageRepository extends PagingAndSortingRepository<SystemMessage, String>, JpaSpecificationExecutor<SystemMessage> {

    List<SystemMessage> findByCreateTimeGreaterThanEqual(Date minCreateTime);

//    List<SystemMessage> findByCreateTimeLessThanOrderByCreateTimeDesc(Date date, Pageable pageable);
//
//    List<SystemMessage> findByCreateTimeGreaterThanOrderByCreateTimeAsc(Date date, Pageable pageable);

    void deleteByMessageTask_Id(String messageTaskId);
}

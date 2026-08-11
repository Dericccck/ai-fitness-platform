package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.List;

public interface MessageTaskForUserRepository extends PagingAndSortingRepository<MessageTaskForUser, String>, JpaSpecificationExecutor<MessageTaskForUser> {
    @Query(value = "select * from message_task_for_user where status = 'INIT' order by create_time desc limit 10000", nativeQuery = true)
    List<MessageTaskForUser> findReadyTask();
}

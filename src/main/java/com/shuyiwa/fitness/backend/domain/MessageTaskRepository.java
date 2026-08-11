package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.List;

public interface MessageTaskRepository extends PagingAndSortingRepository<MessageTask, String>, JpaSpecificationExecutor<MessageTask> {
    @Query(value = "select * from message_task where status='INIT' and publish_time < now() and ( start_publish_time is null or start_publish_time < date_add(now(),interval 1 hour )) and deleted = false", nativeQuery = true)
    List<MessageTask> findReadyTask();
}

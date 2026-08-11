package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.repository.CrudRepository;

import java.util.List;

public interface UserCoachHistoryRepository extends CrudRepository<UserCoachHistory, String>, JpaSpecificationExecutor<UserCoachHistory> {

    List<UserCoachHistory> findByUserIdAndOrganizationIdOrderByCreateTime(String userId,String organizationId);

}

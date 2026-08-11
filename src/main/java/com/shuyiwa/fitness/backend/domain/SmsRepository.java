package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.repository.PagingAndSortingRepository;

public interface SmsRepository extends PagingAndSortingRepository<Sms, String>, JpaSpecificationExecutor<Sms> {
}

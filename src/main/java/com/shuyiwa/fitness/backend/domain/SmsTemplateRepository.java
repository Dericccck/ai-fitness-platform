package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.repository.PagingAndSortingRepository;

public interface SmsTemplateRepository extends PagingAndSortingRepository<SmsTemplate, String> {
}

package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.List;

public interface SmsHistoryRepository extends PagingAndSortingRepository<SmsHistory, String> {

    List<SmsHistory> findByDupCheckCodeAndResult(String dupCheckCode, SmsHistory.SmsResult result);
}

package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.stereotype.Repository;

@Repository
public interface DescContentRepository  extends PagingAndSortingRepository<DescContent, String>, JpaSpecificationExecutor<DescContent> {

}

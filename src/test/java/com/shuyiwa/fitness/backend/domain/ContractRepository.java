package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.repository.PagingAndSortingRepository;

public interface ContractRepository  extends PagingAndSortingRepository<Contract, String>, JpaSpecificationExecutor<Contract> {

}

package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface ContractHistoryRepository  extends CrudRepository<ContractHistory, String>, JpaSpecificationExecutor<ContractHistory> {
    @Query(value = "select * from contract_history where contract_id = :contractId order by create_time asc",nativeQuery = true)
    List<ContractHistory> findByContractId(@Param("contractId") String contractId);

    @Query(value = "select * from contract_history where contract_id = :contractId and update_field = :updateField order by create_time asc",nativeQuery = true)
    List<ContractHistory> findByContractIdAndSignatory(@Param("contractId") String contractId, @Param("updateField")String updateField);
}

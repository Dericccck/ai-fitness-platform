package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface RecordStoreDataRepository extends CrudRepository<RecordStoreData, String>, JpaSpecificationExecutor<RecordStoreData> {

    @Query(value = "select * from record_store_data where deleted = :deleted and create_time < current_date() limit 0,20", nativeQuery = true)
    List<RecordStoreData> findByDeleted(@Param("deleted") boolean deleted);
}

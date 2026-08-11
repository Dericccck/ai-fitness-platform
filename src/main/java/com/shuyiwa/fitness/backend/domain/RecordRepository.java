package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;

public interface RecordRepository extends CrudRepository<Record, String> {

    @Query(value = "insert ignore into record (id,name,create_time) values( :id, :name, now()", nativeQuery = true)
    @Modifying
    int insertIgnore(@Param("id") String id, @Param("name") String name);
}

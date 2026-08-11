package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

@Repository
public interface DevicePushRepository extends CrudRepository<DevicePush, String>, JpaSpecificationExecutor<DevicePush> {

    @Query(value = "update device_push set update_time = now(),status='READY',version = version +1 where id = :id", nativeQuery = true)
    @Modifying
    void devicePushReady(@Param("id") String id);

    @Query(value = "update device_push set update_time = now(),status='DONE',version = version +1 where id = :id", nativeQuery = true)
    @Modifying
    void devicePushDone(@Param("id") String id);
}

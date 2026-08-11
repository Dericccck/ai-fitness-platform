package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface NoticeContainRepository extends CrudRepository<Notice, String>, JpaSpecificationExecutor<Notice> {
    Page<Notice> findByDeleted( boolean deleted, Pageable pageable);

    @Query(value="select * from notice where id = :id",nativeQuery=true)
    Notice findByNoticeId(@Param("id") String id);

    @Query(value="select * from notice where deleted= 0 and status= 1",nativeQuery=true)
    List<Notice> getAllNotDeleted();

}

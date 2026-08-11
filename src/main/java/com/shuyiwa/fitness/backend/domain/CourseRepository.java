package com.shuyiwa.fitness.backend.domain;


import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface CourseRepository extends PagingAndSortingRepository<Course, String>, JpaSpecificationExecutor<Course> {

    @Query("from Course where status=:status")
    List<Course> findAllByStatus(@Param("status") int status);

    @Query(value = "select id from Course where name like :name%",nativeQuery = true)
    List<String> findByName(@Param("name") String name);
}

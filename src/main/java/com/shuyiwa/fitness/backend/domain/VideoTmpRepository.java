package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.List;

public interface VideoTmpRepository extends PagingAndSortingRepository<VideoTmp, String>, JpaSpecificationExecutor<VideoTmp> {

    @Query(value = "select video_id as videoId from video_tmp",nativeQuery = true)
    List<String> getVideoTmp();


}

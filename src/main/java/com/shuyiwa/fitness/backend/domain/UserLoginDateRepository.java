package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

import java.util.Date;
import java.util.List;
import java.util.Map;

public interface UserLoginDateRepository extends PagingAndSortingRepository<UserLoginDate, UserLoginDate.Key> {

    @Modifying
    @Query(value = "insert ignore into user_login_date (login_user_id,login_date) values ( :loginUserId,current_date() ) ", nativeQuery = true)
    int insertIgnore(@Param("loginUserId") String loginUserId);

    List<UserLoginDate> findByLoginUserOrderByLoginDateAsc(LoginUser loginUser);

    @Query(value = "select count(1) value,login_date label from user_login_date where login_date >= :start and login_date <= :end group by login_date order by login_date", nativeQuery = true)
    List<Map<String, Object>> query(@Param("start") Date start, @Param("end") Date end);

}

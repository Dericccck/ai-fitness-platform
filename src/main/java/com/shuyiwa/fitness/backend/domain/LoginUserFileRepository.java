package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;

import java.util.Date;
import java.util.List;

public interface LoginUserFileRepository extends CrudRepository<LoginUserFile, String> {

    List<LoginUserFile> findByLoginUserAndUseTypeAndRemovedAndCreateTimeAfter(LoginUser loginUser, String useTpe, boolean removed, Date createTime);

    @Query(value = "select id from login_user_file where login_user_id = :loginUserId",nativeQuery = true)
    List<String> getAvatarId(String loginUserId);
}

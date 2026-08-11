package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface PhoneVerifyCodeRepository extends CrudRepository<PhoneVerifyCode, String> {
    @Query(value = "select * from Phone_Verify_Code e where e.phone = :phone and create_time < DATE_ADD(now(), INTERVAL :expiredSecond SECOND) and deleted = 0 order by create_time desc limit 1 ", nativeQuery = true)
    Optional<PhoneVerifyCode> findPhoneVerifyCode(@Param("phone") String phone, @Param("expiredSecond") long expiredSecond);
}

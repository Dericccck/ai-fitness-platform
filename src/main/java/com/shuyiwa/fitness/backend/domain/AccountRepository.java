package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface AccountRepository extends CrudRepository<Account, String> {

    Optional<Account> findByLoginUser(Optional<LoginUser> loginUser);

    Optional<Account> findByLoginUserAndCurrencyType(Optional<LoginUser> loginUser, CurrencyType currencyType);

    @Query(value = "insert ignore into account(id,login_user_id,currency_type,balance,version,create_time) values ( REPLACE(UUID(),'-','') , :loginUserId , :currencyType , 0 , 0 ,now() )", nativeQuery = true)
    @Modifying
    void insertIgnore(@Param("loginUserId") String loginUserId, @Param("currencyType") String currencyType);
}

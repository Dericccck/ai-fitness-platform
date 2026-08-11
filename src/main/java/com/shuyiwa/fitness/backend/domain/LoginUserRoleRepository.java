package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.repository.CrudRepository;

import java.util.List;

public interface LoginUserRoleRepository extends CrudRepository<LoginUserRole, String> {
    List<LoginUserRole> findByLoginUser_Id(Long userId);
}

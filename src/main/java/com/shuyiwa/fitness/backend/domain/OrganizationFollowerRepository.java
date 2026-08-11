package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

public interface OrganizationFollowerRepository extends PagingAndSortingRepository<OrganizationFollower, String>, JpaSpecificationExecutor<OrganizationFollower> {
    @Modifying
    @Query(value = "insert ignore into organization_follower (id,organization_id,login_user_id) values ( REPLACE(UUID(),'-',''), :organizationId, :loginUserId )", nativeQuery = true)
    int saveOrIgnore(@Param("organizationId") String organizationId, @Param("loginUserId") String loginUserId);

    Long countByOrganizationId(String organizationId);

    Long countByOrganizationIdAndLoginUserId(String organizationId, String loginUserId);

    void deleteByOrganizationIdAndLoginUserId(String organizationId, String loginUserId);
}

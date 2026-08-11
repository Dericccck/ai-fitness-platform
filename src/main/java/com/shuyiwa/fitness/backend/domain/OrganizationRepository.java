package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

import java.util.Date;
import java.util.List;

public interface OrganizationRepository extends PagingAndSortingRepository<Organization, String>, JpaSpecificationExecutor<Organization> {
    Page<Organization> findByVirtualOrganization(boolean virtualOrganization, Pageable pageable);

    List<Organization> findByName(String name);

    int countByName(String name);

    @Query(value = "select * from organization where match(search) against ( :search )", nativeQuery = true)
    Page<Organization> search(@Param("search") String search, Pageable pageable);

    Page<Organization> findBySearch(String search, Pageable pageable);

    @Query(value = "select * from organization o join organization_applicable_contest_season oa on o.id = oa.organization_id" +
            " where oa.contest_season_id= :contestSeasonId and join_state = 1 order by o.heat desc", nativeQuery = true)
    Page<Organization> topHeatInContestSeason(@Param("contestSeasonId") String contestSeasonId, Pageable pageable);


    @Query(value = "select o.id,o.name,o.logo,o.heat as score from organization o join organization_applicable_contest_season oa on o.id = oa.organization_id" +
            " where oa.contest_season_id= :contestSeasonId and join_state = 1 order by o.heat desc", nativeQuery = true)
    List<OrgRank> organizationHeatRank(@Param("contestSeasonId") String contestSeasonId, Pageable pageable);

    @Query(value = "" +
            "SELECT \n" +
            "    o.id,o.name,o.logo, IFNULL(s1.s, 0) + IFNULL(s2.s, 0) score\n" +
            "FROM\n" +
            "    organization o\n" +
            "        LEFT OUTER JOIN\n" +
            "    (SELECT \n" +
            "        SUM(IF(action = 'vote', 0.1, 0.0001)) s, ci.organization_id\n" +
            "    FROM\n" +
            "        works_action_minute wa\n" +
            "    JOIN works w ON wa.works_id = w.id\n" +
            "    JOIN contestant c ON w.contestant_id = c.id\n" +
            "    JOIN contestant_info ci ON c.contestant_info_id = ci.id\n" +
            "    WHERE\n" +
            "        action_time between :start and :end \n" +
            "            AND ci.deleted = FALSE\n" +
            "            AND c.deleted = FALSE\n" +
            "            AND w.deleted = FALSE\n" +
            "            AND ci.contest_season_id = :contestSeasonId \n" +
            "    GROUP BY organization_id) s1 ON o.id = s1.organization_id\n" +
            "        LEFT OUTER JOIN\n" +
            "    (SELECT \n" +
            "        COUNT(1) s, ci.organization_id\n" +
            "    FROM\n" +
            "        contestant_info ci\n" +
            "    WHERE\n" +
            "        ci.create_time > DATE_ADD(NOW(), INTERVAL - 7 DAY)\n" +
            "            AND ci.deleted = FALSE\n" +
            "            AND ci.contest_season_id = :contestSeasonId \n" +
            "    GROUP BY organization_id) s2 ON o.id = s2.organization_id\n" +
            "ORDER BY score DESC" +
            "", nativeQuery = true)
    List<OrgRank> organizationContributeRank(@Param("contestSeasonId") String contestSeasonId, @Param("start") Date start, @Param("end") Date end, Pageable pageable);

    @Query(value = "" +
            "SELECT \n" +
            "    o.id,o.name,o.logo, IFNULL(s1.s, 0) score\n" +
            "FROM\n" +
            "    organization o\n" +
            "        LEFT OUTER JOIN\n" +
            "    (SELECT \n" +
            "        SUM(IF(action = 'vote', 1, 0)) s, ci.organization_id\n" +
            "    FROM\n" +
            "        works_action_minute wa\n" +
            "    JOIN works w ON wa.works_id = w.id\n" +
            "    JOIN contestant c ON w.contestant_id = c.id\n" +
            "    JOIN contestant_info ci ON c.contestant_info_id = ci.id\n" +
            "    WHERE\n" +
            "        action_time between :start and :end \n" +
            "            AND ci.deleted = FALSE\n" +
            "            AND c.deleted = FALSE\n" +
            "            AND w.deleted = FALSE\n" +
            "            AND ci.contest_season_id = :contestSeasonId \n" +
            "    GROUP BY organization_id) s1 ON o.id = s1.organization_id\n" +
            "ORDER BY score DESC" +
            "", nativeQuery = true)
    List<OrgRank> organizationVoteRank(@Param("contestSeasonId") String contestSeasonId,@Param("start") Date start, @Param("end") Date end,  Pageable pageable);

    @Query(value = "" +
            "SELECT \n" +
            "    o.id,o.name,o.logo, IFNULL(s1.s, 0) score\n" +
            "FROM\n" +
            "    organization o\n" +
            "        LEFT OUTER JOIN\n" +
            "    (SELECT \n" +
            "        SUM(IF(action = 'like', 1, 0)) s, ci.organization_id\n" +
            "    FROM\n" +
            "        works_action_minute wa\n" +
            "    JOIN works w ON wa.works_id = w.id\n" +
            "    JOIN contestant c ON w.contestant_id = c.id\n" +
            "    JOIN contestant_info ci ON c.contestant_info_id = ci.id\n" +
            "    WHERE\n" +
            "        action_time between :start and :end \n" +
            "            AND ci.deleted = FALSE\n" +
            "            AND c.deleted = FALSE\n" +
            "            AND w.deleted = FALSE\n" +
            "            AND ci.contest_season_id = :contestSeasonId \n" +
            "    GROUP BY organization_id) s1 ON o.id = s1.organization_id\n" +
            "ORDER BY score DESC" +
            "", nativeQuery = true)
    List<OrgRank> organizationLikeRank(@Param("contestSeasonId") String contestSeasonId,@Param("start") Date start, @Param("end") Date end,  Pageable pageable);


    @Query(value = "" +
            "SELECT \n" +
            "    o.id,o.name,o.logo,IFNULL(s2.s, 0) score\n" +
            "FROM\n" +
            "    organization o\n" +
            "        LEFT OUTER JOIN\n" +
            "    (SELECT \n" +
            "        COUNT(1) s, ci.organization_id\n" +
            "    FROM\n" +
            "        contestant_info ci\n" +
            "    WHERE\n" +
            "        ci.create_time between :start and :end \n" +
            "            AND ci.deleted = FALSE\n" +
            "            AND ci.contest_season_id = :contestSeasonId \n" +
            "    GROUP BY organization_id) s2 ON o.id = s2.organization_id\n" +
            "ORDER BY score DESC" +
            "", nativeQuery = true)
    List<OrgRank> organizationApplyRank(@Param("contestSeasonId") String contestSeasonId, @Param("start") Date start, @Param("end") Date end, Pageable pageable);

    @Query(value = "" +
            "SELECT \n" +
            "    o.id,o.name,o.logo, IFNULL(s1.s, 0) + IFNULL(s2.s, 0) score\n" +
            "FROM\n" +
            "    organization o\n" +
            "        LEFT OUTER JOIN\n" +
            "    (SELECT \n" +
            "        SUM(IF(action = 'vote', 0.1, 0.0001)) s, ci.organization_id\n" +
            "    FROM\n" +
            "        works_action_minute wa\n" +
            "    JOIN works w ON wa.works_id = w.id\n" +
            "    JOIN contestant c ON w.contestant_id = c.id\n" +
            "    JOIN contestant_info ci ON c.contestant_info_id = ci.id\n" +
            "    WHERE\n" +
            "        action_time > DATE_ADD(NOW(), INTERVAL - 7 DAY)\n" +
            "            AND ci.deleted = FALSE\n" +
            "            AND c.deleted = FALSE\n" +
            "            AND w.deleted = FALSE\n" +
            "            AND ci.contest_season_id = :contestSeasonId \n" +
            "    GROUP BY organization_id) s1 ON o.id = s1.organization_id\n" +
            "        LEFT OUTER JOIN\n" +
            "    (SELECT \n" +
            "        COUNT(1) s, ci.organization_id\n" +
            "    FROM\n" +
            "        contestant_info ci\n" +
            "    WHERE\n" +
            "        ci.create_time > DATE_ADD(NOW(), INTERVAL - 7 DAY)\n" +
            "            AND ci.deleted = FALSE\n" +
            "            AND ci.contest_season_id = :contestSeasonId \n" +
            "    GROUP BY organization_id) s2 ON o.id = s2.organization_id\n" +
            "ORDER BY score DESC" +
            "", nativeQuery = true)
    List<OrgRank> organizationRankInContestSeason(@Param("contestSeasonId") String contestSeasonId, Pageable pageable);

    @Query(value = "" +
            "SELECT \n" +
            "    o.id,o.name,o.logo, IFNULL(s1.s, 0) score\n" +
            "FROM\n" +
            "    organization o\n" +
            "        LEFT OUTER JOIN\n" +
            "    (SELECT \n" +
            "        SUM(IF(action = 'vote', 1, 0)) s, ci.organization_id\n" +
            "    FROM\n" +
            "        works_action_minute wa\n" +
            "    JOIN works w ON wa.works_id = w.id\n" +
            "    JOIN contestant c ON w.contestant_id = c.id\n" +
            "    JOIN contestant_info ci ON c.contestant_info_id = ci.id\n" +
            "    WHERE\n" +
            "        action_time > DATE_ADD(NOW(), INTERVAL - 7 DAY)\n" +
            "            AND ci.deleted = FALSE\n" +
            "            AND c.deleted = FALSE\n" +
            "            AND w.deleted = FALSE\n" +
            "            AND ci.contest_season_id = :contestSeasonId \n" +
            "    GROUP BY organization_id) s1 ON o.id = s1.organization_id\n" +
            "ORDER BY score DESC" +
            "", nativeQuery = true)
    List<OrgRank> organizationVoteInfoRankInContestSeason(@Param("contestSeasonId") String contestSeasonId, Pageable pageable);


    @Query(value = "" +
            "SELECT \n" +
            "    o.id,o.name,o.logo,IFNULL(s2.s, 0) score\n" +
            "FROM\n" +
            "    organization o\n" +
            "        LEFT OUTER JOIN\n" +
            "    (SELECT \n" +
            "        COUNT(1) s, ci.organization_id\n" +
            "    FROM\n" +
            "        contestant_info ci\n" +
            "    WHERE\n" +
            "        ci.create_time > DATE_ADD(NOW(), INTERVAL - 7 DAY)\n" +
            "            AND ci.deleted = FALSE\n" +
            "            AND ci.contest_season_id = :contestSeasonId \n" +
            "    GROUP BY organization_id) s2 ON o.id = s2.organization_id\n" +
            "ORDER BY score DESC" +
            "", nativeQuery = true)
    List<OrgRank> organizationContestantInfoRankInContestSeason(@Param("contestSeasonId") String contestSeasonId, Pageable pageable);


    interface OrgRank {
        String getId();

        String getName();

        String getLogo();

        Double getScore();
    }

    @Query(value = "update organization set next_summary_org_virtual_time = now() where id = :id and next_summary_org_virtual_time is null", nativeQuery = true)
    @Modifying
    int nextSummaryOrgVirtualTimeNowIfEmpty(@Param("id") String id);

    @Query(value = "update organization set next_summary_org_virtual_time = date_add(now(),interval 30 minute) where id = :id", nativeQuery = true)
    @Modifying
    int nextSummaryOrgVirtualTimeLater(@Param("id") String id);

    @Query(value = "" +
            "select * from organization where next_summary_org_virtual_time < now() order by next_summary_org_virtual_time", nativeQuery = true)
    Page<Organization> readySummaryOrgVirtualList(Pageable pageable);

    @Query(value = "SELECT * from organization WHERE audit_status = '0'",nativeQuery= true )
    List<Organization> checkAuditStatus();
}

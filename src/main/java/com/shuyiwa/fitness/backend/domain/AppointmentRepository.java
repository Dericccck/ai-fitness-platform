package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;

import java.util.Date;
import java.util.List;
import java.util.Map;

public interface AppointmentRepository extends CrudRepository<Appointment, String>, JpaSpecificationExecutor<Appointment> {


    //用户或教练查课
    @Query(value="SELECT a FROM Appointment a WHERE (a.user.id = :userId OR a.coach.id = :userId OR a.tempCoach.id = :userId)\n" +
            "AND a.deleted = FALSE AND a.organization.id = :organizationId ORDER BY a.courseStartTime")
    Page<Appointment> findAllByUser(@Param("userId")String userId,@Param("organizationId")String organizationId, Pageable pageable);

    //机构主管查询课程4条sql
    @Query(value="SELECT a FROM Appointment a WHERE \n" +
            "a.deleted = FALSE AND a.organization.id = :organizationId ORDER BY a.courseStartTime")
    Page<Appointment> findAllForAdmin(@Param("organizationId")String organizationId, Pageable pageable);

    @Query(value="SELECT a FROM Appointment a WHERE a.user.id = :userId AND a.coach.id = :coachId \n" +
            "AND a.status = 1 AND a.deleted = FALSE AND a.organization.id = :organizationId ORDER BY a.courseStartTime")
    Page<Appointment> findAllForAdmin(@Param("userId")String userId,@Param("coachId")String coachId,@Param("organizationId")String organizationId, Pageable pageable);

    @Query(value="SELECT a FROM Appointment a WHERE a.user.id = :userId \n" +
            "AND a.deleted = FALSE AND a.organization.id = :organizationId ORDER BY a.courseStartTime")
    Page<Appointment> findAllForAdminUser(@Param("userId")String userId,@Param("organizationId")String organizationId, Pageable pageable);

    @Query(value="SELECT a FROM Appointment a WHERE a.coach.id = :coachId \n" +
            "AND a.deleted = FALSE AND a.organization.id = :organizationId ORDER BY a.courseStartTime")
    Page<Appointment> findAllForAdminCoach(@Param("coachId")String coachId,@Param("organizationId")String organizationId, Pageable pageable);

    //用户或教练查历史课
    @Query(value="SELECT a FROM Appointment a WHERE (a.user.id = :userId OR a.coach.id = :userId OR a.tempCoach.id = :userId)\n" +
            "AND a.deleted = FALSE AND a.organization.id = :organizationId AND a.courseEndTime < :now ORDER BY a.courseStartTime")
    Page<Appointment> findAllByUserHistory(@Param("userId")String userId,@Param("organizationId")String organizationId, Date now, Pageable pageable);

    //机构主管查询历史课程4条sql
    @Query(value="SELECT a FROM Appointment a WHERE \n" +
            " a.deleted = FALSE AND a.organization.id = :organizationId AND a.courseEndTime < :now ORDER BY a.courseStartTime")
    Page<Appointment> findAllForAdminHistory(@Param("organizationId")String organizationId, Date now, Pageable pageable);

    @Query(value="SELECT a FROM Appointment a WHERE a.user.id = :userId AND a.coach.id = :coachId \n" +
            "AND a.deleted = FALSE AND a.organization.id = :organizationId AND a.courseEndTime < :now ORDER BY a.courseStartTime")
    Page<Appointment> findAllForAdminHistory(@Param("userId")String userId,@Param("coachId")String coachId,@Param("organizationId")String organizationId, Date now, Pageable pageable);

    @Query(value="SELECT a FROM Appointment a WHERE a.user.id = :userId \n" +
            "AND a.deleted = FALSE AND a.organization.id = :organizationId AND a.courseEndTime < :now ORDER BY a.courseStartTime")
    Page<Appointment> findAllForAdminUserHistory(@Param("userId")String userId,@Param("organizationId")String organizationId, Date now, Pageable pageable);

    @Query(value="SELECT a FROM Appointment a WHERE a.coach.id = :coachId \n" +
            "AND a.deleted = FALSE AND a.organization.id = :organizationId AND a.courseEndTime < :now ORDER BY a.courseStartTime")
    Page<Appointment> findAllForAdminCoachHistory(@Param("coachId")String coachId,@Param("organizationId")String organizationId, Date now, Pageable pageable);

    @Query(value="select * from appointment where course_start_time<now() and status=:status and deleted=0",nativeQuery = true)
    List<Appointment> findAllByStatus(@Param("status") int status);

    @Query(value="select * from appointment where  contract_id=:contractId and create_time>:createTime and deleted=0 order by create_time asc",nativeQuery = true)
    List<Appointment> findAllByContractId(@Param("contractId") String contractId,@Param("createTime")Date createTime);

    @Query(value="select * from appointment where user_id=:userId and organization_id=:organizationId and status=:status order by  course_end_time desc limit 1",nativeQuery = true)
    List<Appointment> findOneHasFinishedCourse(@Param("userId")String userId,@Param("organizationId")String organizationId,@Param("status") int status);

    @Query(value="select count(*) from appointment where (status=0 or status=1 or status=3 or status=4 or status=5 ) and deleted = false " +
            "and (coach_id = :id or temp_coach_id = :id) and organization_id = :orgId ",nativeQuery = true)
    int countDoing(@Param("id") String id,@Param("orgId") String orgId);

    @Query(value="SELECT count(*) FROM appointment WHERE user_id = :userId\n" +
            "AND organization_id = :orgId \n" +
            "AND deleted = FALSE\n" +
            "AND `status` != 6",nativeQuery = true)
    int findNotFinish(@Param("userId") String userId,@Param("orgId") String orgId);


    @Query(value = "select * from appointment where contract_id=:contractId and organization_id=:organizationId order by course_start_time desc limit 1",nativeQuery = true)
    List<Appointment> findAllByContractId(String contractId, String organizationId);

    @Query(value = "select count(id) from appointment where course_start_date=current_date() and status=6 and organization_id=:organizationId and deleted=0",nativeQuery = true)
    Integer countFinishAppointment(@Param("organizationId") String organizationId);

    @Query(value = "select * from appointment where course_start_date=current_date() and status=6 and organization_id=:organizationId and deleted=0",nativeQuery = true)
    List<Appointment> findFinish(@Param("organizationId") String organizationId);

    @Query(value = "select COUNT(DISTINCT user_id) from appointment where course_start_date=current_date() and status=6 and organization_id=:organizationId and deleted=0",nativeQuery = true)
    Integer countUser(@Param("organizationId") String organizationId);

    @Query(value = "select count(id) from appointment where course_start_date=current_date() and status in(1,4,5,6,7) and organization_id=:organizationId and deleted=0",nativeQuery = true)
    Integer countAppointment(@Param("organizationId") String organizationId);

//    @Query(value = "select * from appointment where course_start_date=DATE_SUB(current_date(), INTERVAL 1 day) and status=6 and deleted=0",nativeQuery = true)
    @Query(value = "select * from appointment where status=6 and deleted=0",nativeQuery = true)
    List<Appointment> findFinished();

//    @Query(value = "select * from appointment where deleted=0 and status=1 and (coach_id=:coachId or temp_coach_id=:coachId) and organization_id=:organizationId and :startDate <= course_start_date and course_start_date <= :endDate",nativeQuery = true)
//    List<Appointment> findByCoachOrTempCoach( String coachId,  Date startDate,  Date endDate,  String organizationId);

    @Query(value = "select * from appointment where deleted=0 and status=1 and coach_id=:coachId and organization_id=:organizationId and :startDate <= course_start_date and course_start_date <= :endDate",nativeQuery = true)
    List<Appointment> findByCoach(String coachId, Date startDate, Date endDate, String organizationId);

    @Query(value = "select count(*) from appointment where contract_id=:contractId and organization_id=:organizationId and deleted = 0",nativeQuery = true)
    Integer countByContract(String contractId, String organizationId);

    @Query(value = "select DATE_FORMAT(confirm_time,'%Y-%m-%d') confirmTime, count(*) finishCount from appointment where  TO_DAYS(NOW( )) - TO_DAYS(confirm_time) = 1 and status = 6 and deleted = 0", nativeQuery = true)
    Map<String,Object> countFinishByYesterday();

    @Query(value = "select count(DISTINCT user_id) from appointment where confirm_time BETWEEN :startDate AND DATE_ADD(:endDate, INTERVAL 1 day) and status = 6 and deleted = 0", nativeQuery = true)
    Integer countClassUser(@Param("startDate") Date startDate, @Param("endDate")Date endDate);
}

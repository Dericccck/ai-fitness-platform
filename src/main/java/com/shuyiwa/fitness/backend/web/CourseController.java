package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.service.NewsService;
import com.shuyiwa.fitness.backend.util.GenCodeUtil;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.*;

import java.util.*;
import java.util.stream.Collectors;

@RestController
public class CourseController {

    @Autowired
    LoginUserRepository loginUserRepository;

    @Autowired
    CourseRepository courseRepository;

    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;

    @Autowired
    UserAndCoachRepository userAndCoachRepository;

    @Autowired
    OrganizationRepository organizationRepository;

    @Autowired
    GenCodeUtil genCodeUtil;

    private static String FIT_COURSE_CODE="FIT:COURSE:CODE";

    @PreAuthorize("isAuthenticated()  && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "保存课程", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/course/save", method = RequestMethod.POST)
    void save(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,@RequestBody Course course
    ) throws FrogException {
        //loginUserAuthorityRepository.findByAuthorityAndLoginUser(Authority.ADMIN_ORGANIZATION,frogUserDetails.getLoginUser(loginUserRepository));

        if(!StringUtils.isEmpty(course.getId())){
            Course coursDb = courseRepository.findById(course.getId()).orElse(null);
           // coursDb.setLastUpdateTime(new Date());
            /*if(course.getCoursePrice()!=null){
                coursDb.setCoursePrice(course.getCoursePrice());
            }
            if(!StringUtils.isEmpty(course.getName())){
                coursDb.setName(course.getName());
            }

            if(null!=course.getStatus()){
                coursDb.setStatus(course.getStatus());
            }*/
            course.setCode(StringUtils.isEmpty(coursDb.getCode())?genCodeUtil.genKocCode(FIT_COURSE_CODE): coursDb.getCode());
//            course.setLastUpdateTime(new Date());
//            course.setCreateTime(coursDb.getCreateTime());
        }else{
            if(StringUtils.isEmpty(course.getName())){
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"课程名称必填");
            }
            if(StringUtils.isEmpty(course.getCoursePrice())){
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"课程价格必填");
            }
            if(StringUtils.isEmpty(course.getOrganization())){
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"机构id必填");
            }
            if(course.getStatus()==null){
                course.setStatus(0);
            }
            course.setCode(genCodeUtil.genKocCode(FIT_COURSE_CODE));
        }
        courseRepository.save(course);
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "分页查询课程列表", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/course/page", method = RequestMethod.GET)
    Page<Course> pageCourse(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam int page,@RequestParam int size,
            @RequestParam String organizationId,
            @RequestParam(required = false) String name,
            @RequestParam(required = false,defaultValue = "false") Boolean courseOpen,
            @RequestParam(required = false,defaultValue = "false") Boolean courseClose
    ) throws FrogException {
        Integer status = null;
        if(courseOpen){
            status = 1;
        }else if(courseClose){
            status = 0;
        }
       // LoginUser loginUser = frogUserDetails.getLoginUser(loginUserRepository);
        PageRequest pageRequest = PageRequest.of(page, size, Sort.by("createTime").descending());
        Specification<Course> empty = Specification.where(null);
        Specification<Course> organizationCondition = StringUtils.isEmpty(organizationId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), organizationId);
        Integer finalStatus = status;
        Specification<Course> statusCondition = StringUtils.isEmpty(status) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("status"), finalStatus);
        Specification<Course> nameCondition = StringUtils.isEmpty(name) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("name"),name+"%");

        return courseRepository.findAll(
                Specification
                        .where(organizationCondition)
                        .and(statusCondition)
                        .and(nameCondition),pageRequest);
    }


    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "获取用户课程列表，如果没有选范围，则返回所有", sinceTime = "2021-06-01")
    @RequestMapping(value = "api/course/list/{userId}", method = RequestMethod.GET)
    public List<Course> findCourseByUser(@PathVariable("userId")String userId,
                                         @RequestParam("organizationId")String organizationId,
                                         @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {

        Organization organization = organizationRepository.findById(organizationId).orElseThrow(()->new FrogException(FrogException.INTERNAL_SERVER_ERROR,"机构不存在"));

        LoginUser user = loginUserRepository.findById(userId).orElseThrow(()->new FrogException(FrogException.INTERNAL_SERVER_ERROR,"用户不存在"));

        UserAndCoach userAndCoach = userAndCoachRepository.findByOrganizationAndUser(organization,user).orElse(null);
        List<Course> list = null;
        if(userAndCoach!=null){
           /* if(!StringUtils.isEmpty(userAndCoach.getUserCourse())){
                String [] courses = userAndCoach.getUserCourse().split(",");
                List<String >ids = Arrays.asList(courses);
                Iterable<Course> coursesi = courseRepository.findAllById(ids);
                if(coursesi!=null){
                    List<Course> finalList = new ArrayList<>();
                    coursesi.forEach(course -> finalList.add(course));
                   list = finalList.stream().filter(course -> course.getStatus()==1).collect(Collectors.toList());
                }
            }else*/{
                list = courseRepository.findAllByStatus(1);
            }
        }
        return list;
    }



}

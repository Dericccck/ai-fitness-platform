package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.LoginUser;
import com.shuyiwa.fitness.backend.domain.LoginUserRepository;
import com.shuyiwa.fitness.backend.domain.UserCoachHistory;
import com.shuyiwa.fitness.backend.domain.UserCoachHistoryRepository;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
public class UserCoachHistoryService {
    private static final Log logger = LogFactory.getLog(UserCoachHistoryService.class);

    @Autowired
    private UserCoachHistoryRepository userCoachHistoryRepository;

    @Autowired
    private LoginUserRepository loginUserRepository;

    @Transactional
    public void save(String userId,String headCoachId,String orgId,String coachId){
        UserCoachHistory userCoachHistory = new UserCoachHistory();
        userCoachHistory.setHeadCoachId(headCoachId);
        userCoachHistory.setCoachId(coachId);
        userCoachHistory.setUserId(userId);
        userCoachHistory.setOrganizationId(orgId);
        userCoachHistory.setCreateTime(new Date());
        userCoachHistoryRepository.save(userCoachHistory);
    }


    public List<UserCoachHistory> findByUserIdAndOrganizationId(String userId, String organizationId){
        List<UserCoachHistory> list = userCoachHistoryRepository.findByUserIdAndOrganizationIdOrderByCreateTime(userId,organizationId);
        list.forEach(userCoachHistory -> {
            String[] headCoachIds = userCoachHistory.getHeadCoachId().split(",");
            String coachName = "";
            int i = 0;
            for (String headCoachId : headCoachIds) {
                LoginUser coach = loginUserRepository.findById(headCoachId).orElse(null);
                if (coach != null){
                    if (i == 0){
                        coachName = coachName + coach.getName();
                    } else {
                        coachName = coachName + "、" + coach.getName();
                    }
                    i++;
                }
            }
            //LoginUser user = loginUserRepository.findById(userCoachHistory.getHeadCoachId()).orElse(null);
            Map<String,Object> map = new HashMap<>();
//            if(user!=null){
//                map.put("coachName",user.getName());
//            }else{
//                map.put("coachName","");
//            }
            map.put("coachName",coachName);
            userCoachHistory.setProperties(map);
        });
        return list;
    }

}

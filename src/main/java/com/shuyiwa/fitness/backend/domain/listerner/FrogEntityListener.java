package com.shuyiwa.fitness.backend.domain.listerner;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.backend.domain.LoginUser;
import com.shuyiwa.fitness.backend.domain.LoginUserRepository;
import com.shuyiwa.fitness.backend.domain.OperationLog;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.service.OperationLogService;
import org.apache.commons.lang.StringUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.hibernate.event.spi.*;
import org.hibernate.persister.entity.EntityPersister;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;

import java.io.Serializable;
import java.util.HashMap;
import java.util.Optional;

@Component
public class FrogEntityListener implements PostUpdateEventListener, PostInsertEventListener, PostDeleteEventListener {
    private static final Log logger = LogFactory.getLog(FrogEntityListener.class);

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    OperationLogService operationLogService;
    @Autowired
    LoginUserRepository loginUserRepository;

    @Override
    public boolean requiresPostCommitHanding(EntityPersister persister) {
        return false;
    }

    @Override
    public boolean requiresPostCommitHandling(EntityPersister persister) {
        return false;
    }

    @Override
    public void onPostUpdate(PostUpdateEvent event) {
        save(event.getClass().getSimpleName(), event.getId(), event.getEntity(), new HashMap() {{
            put("state", event.getState());
            put("oldState", event.getOldState());
        }});
    }

    @Override
    public void onPostInsert(PostInsertEvent event) {
        save(event.getClass().getSimpleName(), event.getId(), event.getEntity(), new HashMap() {{
            put("state", event.getState());
        }});
    }

    private void save(String eventType, Serializable id, Object entity, HashMap state) {
        try {
            if (entity instanceof OperationLog) {
                return;
            }
            Optional<LoginUser> loginUserOptional = Optional.ofNullable(SecurityContextHolder.getContext().getAuthentication())
                    .map(authentication -> authentication.getPrincipal())
                    .filter(principal -> principal instanceof FrogUserDetails)
                    .map(frogUserDetails -> ((FrogUserDetails) frogUserDetails).getLoginUser(loginUserRepository));
            String user = loginUserOptional.map(loginUser -> loginUser.getId() + ":" + loginUser.getPhone()).orElse("unknown:unknown");
            String message = eventType + ":" + ":u:" + user + ":e:" + entity.getClass().getSimpleName() + ":" + id;
            String stateMap = objectMapper.writeValueAsString(state);
            logger.info(message + ":stateMap:" + stateMap);

            OperationLog operationLog = new OperationLog();
            operationLog.setEntityId(StringUtils.substring(id + "", 0, 32));
            operationLog.setEntityName(StringUtils.substring(entity.getClass().getSimpleName(), 0, 50));
            operationLog.setLoginUser(loginUserOptional.orElse(null));
            operationLog.setStateMap(StringUtils.substring(stateMap, 0, Integer.MAX_VALUE));
            operationLog.setEventType(eventType);
            if ("UserLike".equals(operationLog.getEntityName()) || "FrogRankData".equals(operationLog.getEntityName()) || "RankingData".equals(operationLog.getEntityName())) {
                return;
            }
            if ("MessageTaskForUser".equals(operationLog.getEntityName())) {
                return;
            }
            operationLogService.async(() -> operationLogService.saveLog(operationLog));
        } catch (Throwable e) {
            logger.warn("save operation log exception", e);
        }
    }

    @Override
    public void onPostDelete(PostDeleteEvent event) {
        save(event.getClass().getSimpleName(), event.getId(), event.getEntity(), new HashMap() {{
            put("deletedState", event.getDeletedState());
        }});
    }
}

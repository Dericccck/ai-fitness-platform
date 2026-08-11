package com.shuyiwa.fitness.backend.service;

import com.aliyuncs.DefaultAcsClient;
import com.aliyuncs.IAcsClient;
import com.aliyuncs.exceptions.ClientException;
import com.aliyuncs.exceptions.ServerException;
import com.aliyuncs.profile.DefaultProfile;
import com.aliyuncs.push.model.v20160801.PushRequest;
import com.aliyuncs.push.model.v20160801.PushResponse;
import com.aliyuncs.utils.ParameterHelper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.backend.domain.*;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.hibernate.query.criteria.internal.CriteriaBuilderImpl;
import org.hibernate.query.criteria.internal.expression.LiteralExpression;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.expression.spel.standard.SpelExpressionParser;
import org.springframework.expression.spel.support.StandardEvaluationContext;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import javax.persistence.criteria.Expression;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.stream.Collectors;

import static com.shuyiwa.fitness.backend.domain.DevicePushInstance.Status.IGNORE;
import static com.shuyiwa.fitness.backend.domain.DevicePushInstance.Status.INIT;

@Service
public class DevicePushService {
    private static final Log logger = LogFactory.getLog(DevicePushService.class);
    @Autowired
    DevicePushRepository devicePushRepository;
    @Autowired
    DevicePushInstanceRepository devicePushInstanceRepository;
    @Value("${com.shuyiwa.fitness.backend.sms.ali.access-key-id}")
    String accessKeyId;
    @Value("${com.shuyiwa.fitness.backend.sms.ali.access-key-secret}")
    String accessKeySecret;
    @Value("${com.shuyiwa.fitness.backend.push.ali.app:25413634,25419439}")
    String apps;
    @Autowired
    UserTaskService userTaskService;
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    LoginUserTaskProgressRepository loginUserTaskProgressRepository;
    AtomicBoolean checkDevicePushInstanceRunning = new AtomicBoolean(false);

    @Transactional
    public void checkDevicePush() {
        logger.info("checkDevicePush");
        devicePushRepository.findAll(Specification
                        .where((Specification<DevicePush>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("status"), DevicePush.Status.INIT))
                        .and((root, query, criteriaBuilder) -> criteriaBuilder.lessThanOrEqualTo(root.get("schedule"), criteriaBuilder.function("now", Date.class)))
                , PageRequest.of(0, 3))
                .forEach(devicePush -> {
                    String prepareLogic = devicePush.getPrepareLogic();
                    if (!StringUtils.isEmpty(prepareLogic)) {
                        SpelExpressionParser parser = new SpelExpressionParser();
                        StandardEvaluationContext standardEvaluationContext = new StandardEvaluationContext();
                        standardEvaluationContext.setVariable("service", this);
                        standardEvaluationContext.setVariable("instanceRepository", devicePushInstanceRepository);
                        standardEvaluationContext.setVariable("devicePush", devicePush);

                        parser.parseExpression(prepareLogic).getValue(standardEvaluationContext);
                    }
                    devicePushRepository.devicePushReady(devicePush.getId());
                    String nextLogic = devicePush.getNextLogic();
                    if (!StringUtils.isEmpty(nextLogic)) {
                        SpelExpressionParser parser = new SpelExpressionParser();
                        StandardEvaluationContext standardEvaluationContext = new StandardEvaluationContext();
                        standardEvaluationContext.setVariable("service", this);
                        standardEvaluationContext.setVariable("instanceRepository", devicePushInstanceRepository);
                        standardEvaluationContext.setVariable("devicePush", devicePush);

                        parser.parseExpression(nextLogic).getValue(standardEvaluationContext);
                    }

                });
    }

    @Autowired
    ObjectMapper objectMapper;

    public void copyForNextDays(DevicePush devicePush, int days) {
        DevicePush newDevicePush = new DevicePush();
        newDevicePush.setPrepareLogic(devicePush.getPrepareLogic());
        newDevicePush.setCheckLogic(devicePush.getCheckLogic());
        newDevicePush.setNextLogic(devicePush.getNextLogic());
        newDevicePush.setTitle(devicePush.getTitle());
        newDevicePush.setBody(devicePush.getBody());
        newDevicePush.setStatus(DevicePush.Status.INIT);
        newDevicePush.setExpireTime(devicePush.getExpireTime());
        newDevicePush.setStoreOffline(devicePush.isStoreOffline());
        newDevicePush.setSchedule(new Date(devicePush.getSchedule().getTime() + Duration.ofDays(days).toMillis()));
        devicePushRepository.save(newDevicePush);
    }

    public void appliedAndNotFinishTask(String devicePushId, String contestSeasonId, String loginUserTaskId) {
        logger.info("appliedAndNotFinishTask,devicePushId:" + devicePushId + ",contestSeasonId:" + contestSeasonId + ",loginUserTaskId:" + loginUserTaskId);
        Arrays.stream(apps.split(",")).filter(app -> !StringUtils.isEmpty(app))
                .forEach(app -> devicePushInstanceRepository.appliedAndNotFinishTask(devicePushId, contestSeasonId, loginUserTaskId, app));
    }

    @Transactional
    public void checkDevicePushInstance(int mode, int r) {
        if (checkDevicePushInstanceRunning.compareAndSet(false, true)) {
            try {
                innerCheckDevicePushInstance(mode, r);
            } finally {
                checkDevicePushInstanceRunning.set(false);
            }
        }

    }

    private void innerCheckDevicePushInstance(int mode, int r) {
        logger.info("checkDevicePushInstance,mode:" + mode + ",r:" + r);
        devicePushRepository.findAll(Specification
                        .where((Specification<DevicePush>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("status"), DevicePush.Status.READY))
                , PageRequest.of(0, 3)
        ).forEach(devicePush -> {
            AtomicBoolean empty = new AtomicBoolean(true);
            logger.info("empty:" + empty.get());
            Arrays.stream(apps.split(",")).filter(app -> !StringUtils.isEmpty(app))
                    .forEach(app -> {
                        long appKey = Long.parseLong(app);
                        List<DevicePushInstance> batch = devicePushInstanceRepository.findAll(Specification
                                        .where((Specification<DevicePushInstance>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("status"), DevicePushInstance.Status.INIT))
                                        .and((root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("app"), app))
                                        .and((root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("devicePush").get("id"), devicePush.getId()))
                                        .and((root, query, criteriaBuilder) -> {
                                            Expression<Integer> m = criteriaBuilder.function("modulus", Integer.class, root.get("id"), new LiteralExpression<>((CriteriaBuilderImpl) criteriaBuilder, mode));
                                            return criteriaBuilder.equal(m, r);
                                        })
                                , PageRequest.of(0, 100)
                        ).getContent().stream().collect(Collectors.toList());
                        if (batch.size() != 0) {
                            empty.set(false);
                            logger.info("empty:1:" + empty.get());
                            batch.forEach(devicePushInstance -> {
                                String checkLogic = devicePushInstance.getDevicePush().getCheckLogic();
                                if (!StringUtils.isEmpty(checkLogic)) {
                                    SpelExpressionParser parser = new SpelExpressionParser();
                                    StandardEvaluationContext standardEvaluationContext = new StandardEvaluationContext();
                                    standardEvaluationContext.setVariable("service", this);
                                    standardEvaluationContext.setVariable("instanceRepository", devicePushInstanceRepository);
                                    standardEvaluationContext.setVariable("devicePushInstance", devicePushInstance);
                                    Object value = parser.parseExpression(checkLogic).getValue(standardEvaluationContext);
                                    logger.info("check:" + devicePushInstance.getLoginUser().getId() + ",result:" + value);
                                    if (!Boolean.TRUE.equals(value)) {
                                        devicePushInstance.setStatus(IGNORE);
                                        devicePushInstanceRepository.devicePushInstanceIgnore(devicePushInstance.getId());
                                    }
                                }
                            });

                            List<DevicePushInstance> filteredBatch = batch.stream().filter(devicePushInstance -> devicePushInstance.getStatus() == INIT).collect(Collectors.toList());
                            String targetValue = filteredBatch.stream().map(DevicePushInstance::getLoginUser).map(LoginUser::getId).collect(Collectors.joining(","));
                            logger.info("targetValue:" + targetValue);


                            sendPush(devicePush, appKey, filteredBatch, targetValue);
                        } else {
                            if (devicePushInstanceRepository.findAll(Specification
                                            .where((Specification<DevicePushInstance>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("status"), DevicePushInstance.Status.INIT))
                                            .and((root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("app"), app))
                                            .and((root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("devicePush").get("id"), devicePush.getId()))
                                    , PageRequest.of(0, 1)).stream().count() != 0) {
                                empty.set(false);
                                logger.info("empty:2:" + empty.get());
                            }
                        }


                    });

            logger.info("empty:3:" + empty.get());
            if (empty.get()) {
                devicePushRepository.devicePushDone(devicePush.getId());
            }
        });
    }

    private void sendPush(DevicePush devicePush, long appKey, List<DevicePushInstance> filteredBatch, String targetValue) {
        DefaultProfile profile = DefaultProfile.getProfile("cn-hangzhou", accessKeyId, accessKeySecret);
        IAcsClient client = new DefaultAcsClient(profile);
        PushRequest request = new PushRequest();
        request.setAppKey(appKey);
        request.setPushType("NOTICE");
        request.setDeviceType("ALL");
        request.setTarget("ACCOUNT");
        request.setTargetValue(targetValue);
        request.setTitle(devicePush.getTitle());
        request.setBody(devicePush.getBody());
        request.setAndroidNotificationChannel("1");
        request.setStoreOffline(devicePush.isStoreOffline());
        if (devicePush.getExpireTime() != null) {
            request.setExpireTime(ParameterHelper.getISO8601Time(devicePush.getExpireTime()));
        }
        try {
            PushResponse response = client.getAcsResponse(request);
            String requestId = response.getRequestId();
            logger.info("requestId:" + requestId);
            String messageId = response.getMessageId();
            logger.info("messageId:" + messageId);
            filteredBatch.forEach(devicePushInstance -> {
                devicePushInstanceRepository.devicePushInstanceFinish(devicePushInstance.getId(), requestId, messageId);
            });
        } catch (ServerException e) {
            logger.warn("checkDevicePushInstance exception:ServerException", e);
            filteredBatch.forEach(devicePushInstance -> {
                devicePushInstanceRepository.devicePushInstanceFailed(devicePushInstance.getId());
            });
        } catch (ClientException e) {
            logger.warn("checkDevicePushInstance exception:ClientException", e);
            filteredBatch.forEach(devicePushInstance -> {
                devicePushInstanceRepository.devicePushInstanceFailed(devicePushInstance.getId());
            });
        }
    }

    public boolean isFinishTask(String loginUserTaskId, String loginUserId) {
        List<LoginUserTask> taskList = new ArrayList<>();
        taskList.add(userTaskService.cachedTask(loginUserTaskId));
        userTaskService.checkTaskProgress(loginUserRepository.findById(loginUserId).get(), taskList);
        return loginUserTaskProgressRepository.findByLoginUser_IdAndLoginUserTask_Id(loginUserId, loginUserTaskId).map(p -> p.getCompleteTime() != null).orElse(false);
    }

}

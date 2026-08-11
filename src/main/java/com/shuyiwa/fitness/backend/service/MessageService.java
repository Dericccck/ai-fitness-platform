package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.bean.RedeemCopyItem;
import com.shuyiwa.fitness.backend.event.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.web.FeedController;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import javax.persistence.EntityManager;
import java.text.SimpleDateFormat;
import java.time.Duration;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Optional;

import static com.shuyiwa.fitness.backend.domain.Sms.Template.APPLY_SUCCESS_NOTIFY;
import static com.shuyiwa.fitness.backend.domain.Sms.Template.REG_SUCCESS_NOTIFY;

@Service
public class MessageService {
    private static final Log logger = LogFactory.getLog(MessageService.class);
    @Autowired
    UserMessageRepository userMessageRepository;
    @Autowired
    SystemMessageRepository systemMessageRepository;
    @Autowired
    MessageTaskRepository messageTaskRepository;
    @Autowired
    EntityManager entityManager;
    @Autowired
    SmsService smsService;
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    ContestantInfoRepository contestantInfoRepository;
    @Autowired
    UserTaskService userTaskService;
    @Autowired
    ContestScheduleRepository contestScheduleRepository;
    @Autowired
    MessageTaskForUserRepository messageTaskForUserRepository;
    @Autowired
    MessageTaskForUserService messageTaskForUserService;

    @Transactional(rollbackFor = Throwable.class)
    public void receiveSystemMessage(LoginUser loginUser) {
        Date minCreateTime = new Date(System.currentTimeMillis() - Duration.ofDays(36500).toMillis());
        Optional<UserMessage> lastSystemMessage = userMessageRepository.findTop1ByLoginUser_IdAndMessageTypeOrderByCreateTimeDesc(loginUser.getId(), UserMessage.MessageType.SYSTEM);
        if (lastSystemMessage.isPresent()) {
            minCreateTime = lastSystemMessage.get().getCreateTime();
        }
        logger.info("receiveSystemMessage:minCreateTime:" + new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX").format(minCreateTime));
        List<SystemMessage> systemMessages = systemMessageRepository.findByCreateTimeGreaterThanEqual(minCreateTime);
        for (SystemMessage systemMessage : systemMessages) {
            logger.info("receiveSystemMessage:systemMessage:" + systemMessage.getId());
            Optional<UserMessage> userMessageOptional = userMessageRepository.findBySystemMessageIdAndLoginUser(systemMessage.getId(), loginUser).stream().findFirst();
            if (!userMessageOptional.isPresent()) {
                UserMessage userMessage = systemMessage2userMessage(systemMessage);
                userMessage.setLoginUser(loginUser);
                saveUserMessage(userMessage);
            }
        }
    }

    @Transactional
    public UserMessage saveUserMessage(UserMessage userMessage) {
        if (userMessage.getId() == null) {
            if (userMessage.getMessageTask() != null) {
                if (userMessageRepository.findByLoginUserAndMessageTask(userMessage.getLoginUser(), userMessage.getMessageTask()).size() > 0) {
                    logger.info("user message for task is dup:" + userMessage.getLoginUser().getId() + ",message_task:" + userMessage.getMessageTask().getId());
                    return null;
                }
            }
        }
        return userMessageRepository.save(userMessage);
    }

    public UserMessage systemMessage2userMessage(SystemMessage systemMessage) {
        UserMessage userMessage = new UserMessage();
        userMessage.setMessageTask(systemMessage.getMessageTask());
        //lizf，现在先暂时显示官方账号消息，跟管理员账号无关
//        userMessage.setSourceLoginUser(systemMessage.getSourceLoginUser());
        userMessage.setCreateTime(systemMessage.getCreateTime());
        userMessage.setMessageType(UserMessage.MessageType.SYSTEM);
        userMessage.setCreateTime(systemMessage.getCreateTime());
        userMessage.setContent(systemMessage.getContent());
        userMessage.setSystemMessageId(systemMessage.getId());
        userMessage.setStatus(UserMessage.UserMessageStatus.UNREAD);
        userMessage.setTag(systemMessage.getTag());
        userMessage.setLinkEntity(systemMessage.getLinkEntity());
        userMessage.setLinkEntityType(systemMessage.getLinkEntityType());
        userMessage.setLinkText(systemMessage.getLinkText());
        return userMessage;
    }

    @Transactional
    public MessageTask saveMessageTask(MessageTask messageTask) {
        if (messageTask.getChannel().contains(MessageTask.Channel.SMS.name())) {
            messageTask.setContent(null);
        } else {
            messageTask.setSmsTemplate(null);
        }
        messageTaskRepository.save(messageTask);
        entityManager.flush();
        if (messageTask.getCreateTime() == null) {
            entityManager.refresh(messageTask);
        }
        return messageTask;
    }

    @Transactional
    public void deleteMessageTask(String messageTaskId) {
        userMessageRepository.deleteByMessageTask_Id(messageTaskId);
        systemMessageRepository.deleteByMessageTask_Id(messageTaskId);
        messageTaskRepository.findById(messageTaskId).ifPresent(messageTask -> {
            messageTask.setDeleted(true);
            messageTaskRepository.save(messageTask);
        });
    }

    @Transactional
    public MessageTask publish(String id, Date publishTime) throws FrogException {
        Optional<MessageTask> taskOptional = messageTaskRepository.findById(id);
        if (taskOptional.isPresent()) {
            MessageTask messageTask = taskOptional.get();
            if (publishTime == null) {
                messageTask.setPublishTime(contestScheduleRepository.now());
                messageTaskRepository.save(messageTask);
                return messageTask;
            } else {
                messageTask.setPublishTime(publishTime);
                messageTaskRepository.save(messageTask);
                return messageTask;
            }
        }
        return null;
    }

    public void checkMessageTask() {
        List<MessageTask> readyTasks = messageTaskRepository.findReadyTask();
        logger.warn("readyTasks:" + readyTasks.size());
        for (MessageTask messageTask : readyTasks) {
            messageTaskForUserService.saveSystemMessage(messageTask);
            messageTaskForUserService.createProgress(messageTask);
            messageTask.setStatus(MessageTask.TaskStatus.PUBLISHED);
            messageTaskRepository.save(messageTask);
            logger.info("saved:messageTask");
        }
    }

    public void checkMessageTaskProgress() {
        List<MessageTaskForUser> readyTasks = messageTaskForUserRepository.findReadyTask();
        logger.warn("checkMessageTaskProgress:" + readyTasks.size());
        for (MessageTaskForUser messageTaskForUser : readyTasks) {
            try {
                messageTaskForUserService.progress(messageTaskForUser);
            } catch (Exception e) {
                logger.info("messageTaskForUser:" + messageTaskForUser.getId() + " failed", e);
            }
        }
    }


    @EventListener
    @Transactional
    public void handleLoginUserCreatedEvent(LoginUserCreatedEvent event) {
        logger.info("LoginUserCreatedEvent");
        UserMessage userMessage = new UserMessage();
        String id = event.getLoginUser().getId();
        if (id != null) {

            LoginUser loginUser = loginUserRepository.findById(id).orElse(null);
            if (loginUser != null) {
                userMessage.setLoginUser(loginUser);
                userMessage.setMessageType(UserMessage.MessageType.USER);
                userMessage.setContent("欢迎加入树艺蛙O(∩_∩)O\n" +
                        "“首届树艺蛙全国少儿才艺大赛”报名进行中，大奖就在眼前，赶快关注比赛详情，让咱们家娃娃闪耀起来吧！");
                saveUserMessage(userMessage);

                try {
                    smsService.send(loginUser.getPhone(), new HashMap<>(), REG_SUCCESS_NOTIFY);
                } catch (FrogException e) {
                    logger.info("handleLoginUserCreatedEvent exception", e);
                }
            }
        }
    }

    @EventListener
    @Transactional
    public void handleContestantInfoCreatedEvent(ContestantInfoCreatedEvent event) {
        logger.info("ContestantInfoCreatedEvent");
        UserMessage userMessage = new UserMessage();
        LoginUser loginUser = event.getContestantInfo().getAgentLoginUser();
        if (event.getContestantInfo() != null && loginUser != null) {
            ContestSeason contestSeason = event.getContestantInfo().getContestSeason();
            if (contestSeason != null) {
                String phone = loginUser.getPhone();
                try {
                    Optional<ContestantInfo> contestantInfoOptional = contestantInfoRepository.findByAgentLoginUserAndContestSeason_IdAndDeleted(loginUser, contestSeason.getId(), false)
                            .stream().findFirst();
                    if (contestantInfoOptional.isPresent() && contestantInfoOptional.get().getId().equals(event.getContestantInfo().getId())) {
                        //由于对于一个手机号，既可能是领队，有可能是多个成员的联系账号，这里要保证针对同一个手机号，同一个比赛，只发一次成功报名信息
                        logger.info("handleContestantInfoCreatedEvent:ok:" + phone);
                        userMessage.setLoginUser(loginUser);
                        userMessage.setMessageType(UserMessage.MessageType.USER);
                        userMessage.setContent("您已成功报名【" + contestSeason.getName() + "】，记得要随时关注活动进展哟！");
                        saveUserMessage(userMessage);
                        try {
                            smsService.send(phone, new HashMap<>(), APPLY_SUCCESS_NOTIFY);
                        } catch (FrogException e) {
                            logger.info("handleContestantInfoCreatedEvent exception", e);
                        }
                    } else {
                        logger.info("handleContestantInfoCreatedEvent:ignore:" + phone);
                    }
                } catch (Exception e) {
                    logger.info("handleContestantInfoCreatedEvent:error:" + phone, e);
                }
            }
        }
    }

    @EventListener
    @Transactional
    public void handleWorksUploadEvent(WorksUploadEvent event) {
        logger.info("LoginUserCreatedEvent");
        UserMessage userMessage = new UserMessage();
        LoginUser loginUser = event.getWorks().getLoginUser();
        if (loginUser != null) {
            if (event.getWorks() != null) {
                if (event.getWorks().getFormat() == Works.WorksFormat.VIDEO) {
                    userMessage.setLoginUser(loginUser);
                    userMessage.setMessageType(UserMessage.MessageType.USER);
                    userMessage.setContent("您的作品正在审核中，请稍候。");
                    saveUserMessage(userMessage);
                }
            }
        }
    }

    @EventListener
    @Transactional
    public void handleWorksAuditSuccessEvent(WorksAuditSuccessEvent event) {
        logger.info("LoginUserCreatedEvent");
        UserMessage userMessage = new UserMessage();
        LoginUser loginUser = event.getWorks().getLoginUser();
        if (loginUser != null) {
            userMessage.setLoginUser(loginUser);
            userMessage.setMessageType(UserMessage.MessageType.USER);
            userMessage.setContent("您的作品<" + event.getWorks().getName() + ">已审核通过！");
            saveUserMessage(userMessage);
        }
    }

    @EventListener
    @Transactional
    public void handleWorksAuditSuccessEvent(WorksAuditFailedEvent event) {
        logger.info("LoginUserCreatedEvent");
        UserMessage userMessage = new UserMessage();
        LoginUser loginUser = event.getWorks().getLoginUser();
        if (loginUser != null) {
            userMessage.setLoginUser(loginUser);
            userMessage.setMessageType(UserMessage.MessageType.USER);
            userMessage.setContent("您的作品<" + event.getWorks().getName() + ">审核失败了……");
            saveUserMessage(userMessage);
        }
    }

    @EventListener
    @Transactional
    public void handleLoginUserTaskProgressCompletedEvent(LoginUserTaskProgressCompletedEvent event) {
        logger.info("LoginUserTaskProgressCompletedEvent");
        UserMessage userMessage = new UserMessage();
        LoginUser loginUser = event.getLoginUserTaskProgress().getLoginUser();
        if (loginUser != null) {
            userMessage.setLoginUser(loginUser);
            userMessage.setMessageType(UserMessage.MessageType.USER);
            UserTaskService.Context context = userTaskService.newTaskContext(loginUser);
            userMessage.setContent("您获得了<" + userTaskService.resolvedLogicDesc(context, event.getLoginUserTaskProgress().getLoginUserTask()) + ">，努力获得更多勋章吧！有些勋章有特殊奖励，请到【我的勋章】看看吧！");
            saveUserMessage(userMessage);
        }
    }

    @EventListener
    @Transactional
    public void handleRedeemEvent(RedeemEvent event) {
        logger.info("RedeemEvent");
        UserMessage userMessage = new UserMessage();
        loginUserRepository.findById(event.getLoginUserId()).ifPresent(loginUser -> {
            userMessage.setLoginUser(loginUser);
            userMessage.setMessageType(UserMessage.MessageType.USER);
            UserTaskService.Context context = userTaskService.newTaskContext(loginUser);
            StringBuilder content = new StringBuilder();
            for (RedeemCopyItem redeemCopyItem : event.getCopyItems()) {
                switch (redeemCopyItem.getDataType()) {
                    case "TITLE":
                        content.append(redeemCopyItem.getContent()).append("\n");
                        break;
                    case "LINK":
                        content.append(redeemCopyItem.getContent()).append(":").append(redeemCopyItem.getExtra()).append("\n");
                        break;
                    case "COUPONLIB":
                        content.append(redeemCopyItem.getContent()).append(":").append(redeemCopyItem.getExtra()).append("\n");
                        break;
                    default:
                        break;

                }
            }
            userMessage.setContent(content.toString());
            saveUserMessage(userMessage);
        });
    }


    public void fill(UserMessage userMessage, Date now) {
        if (userMessage != null) {
            userMessage.setProperty("now", now);
        }
        if (userMessage.getLinkEntityType() != null && userMessage.getLinkEntity() != null) {
            switch (userMessage.getLinkEntityType()) {
                case IN_LINK:
                    userMessage.setProperty("linkAction", new FeedController.Action(FeedItem.EntityType.IN_LINK.name(), userMessage.getLinkEntity()));
                    break;
                case WORKS:
                    userMessage.setProperty("linkAction", new FeedController.Action(FeedItem.EntityType.WORKS.name(), userMessage.getLinkEntity()));
                    break;
            }
        }
    }
}

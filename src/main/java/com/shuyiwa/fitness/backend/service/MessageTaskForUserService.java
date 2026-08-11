package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Optional;
import java.util.stream.Collectors;

@Service
public class MessageTaskForUserService {
    private static final Log logger = LogFactory.getLog(MessageTaskForUserService.class);

    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    ContestantInfoRepository contestantInfoRepository;
    @Autowired
    MessageTaskForUserRepository messageTaskForUserRepository;
    @Autowired
    MessageService messageService;
    @Autowired
    SmsService smsService;
    @Autowired
    ContestScheduleRepository contestScheduleRepository;
    @Autowired
    SystemMessageRepository systemMessageRepository;

    @Transactional
    public void createProgress(MessageTask messageTask) {
        List<MessageTask.Receiver> receivers = Arrays.stream(messageTask.getReceiver().split(","))
                .filter(t -> !StringUtils.isEmpty(t))
                .map(t -> {
                    try {
                        return MessageTask.Receiver.valueOf(t);
                    } catch (IllegalArgumentException e) {
                        return null;
                    }
                })
                .filter(o -> o != null)
                .collect(Collectors.toList());
        for (MessageTask.Receiver receiver : receivers) {
            switch (receiver) {
                case ALL:
                    loginUserRepository.insertAllToMessageTaskUser(messageTask.getId());
                    break;
                case APPLY:
                    contestantInfoRepository.insertAllToMessageTaskUser(messageTask.getId());
//                case TEST:
//
//                    break;
                default:
                    logger.warn("unknown receiver:" + receiver);
            }
        }

        Arrays.stream(Optional.ofNullable(messageTask).map(MessageTask::getPhoneList).orElse("").split("[^\\d]")).distinct().forEach(phone -> {
            loginUserRepository.findByPhone(phone).ifPresent(loginUser ->
            {
                Specification<MessageTaskForUser> loginUserSp = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("loginUser"), loginUser);
                Specification<MessageTaskForUser> messageTaskSp = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("messageTask"), messageTask);
                long count = messageTaskForUserRepository.count(Specification.where(loginUserSp).and(messageTaskSp));
                if (count < 1) {
                    MessageTaskForUser p = new MessageTaskForUser();
                    p.setStatus(MessageTaskForUser.TaskStatus.INIT);
                    p.setLoginUser(loginUser);
                    p.setMessageTask(messageTask);
                    messageTaskForUserRepository.save(p);
                }
            });
        });
    }


//    @Transactional
//    public void save(List<LoginUser> loginUserIdList, MessageTask messageTask) {
//        List<MessageTaskForUser> collect = loginUserIdList.stream().map(loginUser -> {
//
//            MessageTaskForUser p = new MessageTaskForUser();
//            p.setStatus(MessageTaskForUser.TaskStatus.INIT);
//            p.setLoginUser(loginUser);
//            p.setMessageTask(messageTask);
//            return p;
//        }).collect(Collectors.toList());
//        messageTaskForUserRepository.saveAll(collect);
//    }

    @Transactional
    public void progress(MessageTaskForUser messageTaskForUser) {
        List<MessageTask.Channel> channelList = Arrays.stream(messageTaskForUser.getMessageTask().getChannel().split(","))
                .filter(t -> !StringUtils.isEmpty(t))
                .map(t -> MessageTask.Channel.valueOf(t))
                .collect(Collectors.toList());
        for (MessageTask.Channel channel : channelList) {
            switch (channel) {
                case APP:
                    publishUseApp(messageTaskForUser.getMessageTask(), messageTaskForUser.getLoginUser());
                    break;
                case SMS:
                    publishUseSms(messageTaskForUser);
                    break;
                default:
                    logger.warn("unknown channel: " + channel);
            }
        }
        messageTaskForUser.setStatus(MessageTaskForUser.TaskStatus.FINISHED);
        messageTaskForUser.setUpdateTime(contestScheduleRepository.cachedNow());
        messageTaskForUserRepository.save(messageTaskForUser);
    }

    private void publishUseApp(MessageTask messageTask, LoginUser loginUser) {
        String content = messageTask.getContent();
        if (StringUtils.isEmpty(content)) {
            HashMap<String, String> params = new HashMap<>();
            content = messageTask.getSmsTemplate().getResolvedContent(params);
        }
        UserMessage userMessage = new UserMessage();
        userMessage.setLoginUser(loginUser);
        userMessage.setMessageType(UserMessage.MessageType.USER);
        userMessage.setContent(content);
        userMessage.setLinkEntity(messageTask.getLinkEntity());
        userMessage.setLinkEntityType(messageTask.getLinkEntityType());
        userMessage.setLinkText(messageTask.getLinkText());
        //不设置消息来源用户，默认为树艺蛙官方
//        userMessage.setSourceLoginUser(messageTask.getSourceLoginUser());
        userMessage.setMessageTask(messageTask);
        messageService.saveUserMessage(userMessage);
    }

    private void publishUseSms(MessageTaskForUser messageTaskForUser) {
        try {
            sendSms(messageTaskForUser);
        } catch (FrogException e) {
            messageTaskForUser.setSmsResult("exception:" + e.getMessage());
        }
    }

    private void sendSms(MessageTaskForUser messageTaskForUser) throws FrogException {
        logger.warn("sendSms:0:");
        LoginUser loginUser = messageTaskForUser.getLoginUser();
        MessageTask messageTask = messageTaskForUser.getMessageTask();
        Sms.Template template = messageTask.getSmsTemplate();
        if (template != null) {
            Sms sms = smsService.send(loginUser.getPhone(), getParams(loginUser, template), template, messageTask);
            logger.warn("sendSms:1:" + ",result:" + sms.getResult());
            messageTaskForUser.setSmsResult(sms.getResult().name());
            messageTaskForUser.setSmsResponse(sms.getResponse());
        } else {
            messageTaskForUser.setSmsResult("template not exist");
        }
    }

    private HashMap<String, String> getParams(LoginUser loginUser, Sms.Template template) {
        logger.warn("getParamsgetParams:0:");
        HashMap<String, String> params = new HashMap<>();
        String name = loginUser.getName();
        params.put("name", StringUtils.isEmpty(name) ? loginUser.getPhone() : loginUser.getName());
        logger.warn("getParamsgetParams:1:" + params.get("name"));
        logger.warn("getParamsgetParams:1:" + params.get("name") + ":getContestSeasonId:" + template.getContestSeasonId());
        if (template.getContestSeasonId() != null) {
            contestantInfoRepository.findByAgentLoginUserAndContestSeason_IdAndDeleted(loginUser, template.getContestSeasonId(), false)
                    .stream().map(ContestantInfo::getName).filter(contestInfoName -> !StringUtils.isEmpty(contestInfoName)).findFirst().ifPresent(contestInfoName -> {
                params.put("name", contestInfoName);
                logger.warn("getParamsgetParams:2:" + contestInfoName);
            });
        }
        return params;
    }

    @Transactional
    public void saveSystemMessage(MessageTask messageTask) {
        List<MessageTask.Receiver> receivers = Arrays.stream(messageTask.getReceiver().split(","))
                .filter(t -> !StringUtils.isEmpty(t))
                .map(t -> MessageTask.Receiver.valueOf(t))
                .collect(Collectors.toList());
        if (receivers.stream().filter(receiver -> receiver == MessageTask.Receiver.ALL).findAny().isPresent()) {
            SystemMessage systemMessage = new SystemMessage();
            systemMessage.setContent(messageTask.getContent());
            systemMessage.setLinkEntity(messageTask.getLinkEntity());
            systemMessage.setLinkEntityType(messageTask.getLinkEntityType());
            systemMessage.setLinkText(messageTask.getLinkText());
            systemMessage.setSourceLoginUser(messageTask.getSourceLoginUser());
            systemMessage.setMessageTask(messageTask);
            systemMessageRepository.save(systemMessage);
        }
    }
}

package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.service.MessageService;
import org.apache.commons.lang.StringUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.*;
import java.util.stream.Collectors;

@RestController
public class MessageController {
    private static final Log logger = LogFactory.getLog(MessageController.class);
    @Autowired
    UserMessageRepository userMessageRepository;
    @Autowired
    MessageService messageService;

    @Autowired
    SystemMessageRepository systemMessageRepository;

    @Autowired
    SmsTemplateRepository smsTemplateRepository;
    @Autowired
    MessageTaskRepository messageTaskRepository;
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    ContestScheduleRepository contestScheduleRepository;

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "消息页面用到词典数据")
    @RequestMapping(value = "api/message/dict", method = RequestMethod.GET)
    HashMap<String, Map> dict() {
        return new HashMap<String, Map>() {{
            put("channel", Arrays.stream(MessageTask.Channel.values()).collect(Collectors.toMap(e -> e, e -> e.getDesc())));
            put("taskStatus", Arrays.stream(MessageTask.TaskStatus.values()).collect(Collectors.toMap(e -> e, e -> e.getDesc())));
            put("receiver", Arrays.stream(MessageTask.Receiver.values()).collect(Collectors.toMap(e -> e, e -> e.getDesc())));
        }};
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "消息模版列表")
    @RequestMapping(value = "api/message/sms/template", method = RequestMethod.GET)
    List<Sms.Template> templateList() {
        List<Sms.Template> collect = Arrays.stream(Sms.Template.values())
                .filter(template -> !template.isOnlyAuto())
                .collect(Collectors.toList());
        collect.forEach(template -> template.setProperty("id", template.name()));
        return collect;
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "分页获取消息任务")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/message/task/page", method = RequestMethod.GET)
    Page<MessageTask> fetch(@RequestParam int page, @RequestParam int size) {
        return messageTaskRepository.findAll(
                Specification.where((Specification<MessageTask>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false)),
                PageRequest.of(page, size, Sort.by("createTime").descending()));
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "保存消息任务")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN') || hasAuthority('ADMIN_MESSAGES') )")
    @RequestMapping(value = "api/message/task", method = RequestMethod.POST)
    MessageTask saveMessageTask(@RequestBody MessageTask messageTask, @AuthenticationPrincipal FrogUserDetails frogUserDetails) {
        if (messageTask.getSourceLoginUser() == null) {
            messageTask.setSourceLoginUser(frogUserDetails.getLoginUser(loginUserRepository));
        }
        return messageService.saveMessageTask(messageTask);
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "删除消息任务")
    @PreAuthorize("isAuthenticated() && ( hasAuthority('ADMIN') || hasAuthority('ADMIN_MESSAGES') )")
    @RequestMapping(value = "api/message/task", method = RequestMethod.DELETE)
    void delete(@RequestParam("id") String id) {
        messageService.deleteMessageTask(id);
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Api}, desc = "我的消息列表")
    @RequestMapping(value = "api/my/message", method = RequestMethod.GET)
    List<UserMessage> messageList(
            @RequestParam(value = "ot", required = false, defaultValue = "") String ot,
            @RequestParam(value = "nt", required = false, defaultValue = "") String nt,
            @RequestParam(value = "limit", required = false) int limit,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        if (!StringUtils.isEmpty(ot) && !StringUtils.isEmpty(nt)) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "ot nt不能同时指定");
        }
        List<UserMessage> messageList;
        if (frogUserDetails == null) {
            Specification<SystemMessage> createTimeCondition = Specification.where(null);
            Sort sort = Sort.by("createTime").descending();
            if (!StringUtils.isEmpty(ot)) {
                //上拉刷新，
                createTimeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.lessThan(root.get("createTime"), new Date(Long.parseLong(ot)));
            } else if (!StringUtils.isEmpty(nt)) {
                //下拉刷新
                createTimeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.greaterThan(root.get("createTime"), new Date(Long.parseLong(nt)));
                sort = Sort.by("createTime").ascending();
            }
            messageList = systemMessageRepository.findAll(Specification.where(createTimeCondition), PageRequest.of(0, limit, sort))
                    .stream().sorted((m1, m2) -> m2.getCreateTime().compareTo(m1.getCreateTime()))
                    .map(m -> addScore(m)).collect(Collectors.toList());

        } else {
            Specification<UserMessage> loginUserCondition = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("loginUser").get("id"), frogUserDetails.getLoginUser(loginUserRepository).getId());
            Specification<UserMessage> createTimeCondition = Specification.where(null);
            Sort sort = Sort.by("createTime").descending();

            if (!StringUtils.isEmpty(ot)) {
                //上拉刷新，
                createTimeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.lessThan(root.get("createTime"), new Date(Long.parseLong(ot)));
            } else if (!StringUtils.isEmpty(nt)) {
                //下拉刷新
                sort = Sort.by("createTime").ascending();
                createTimeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.greaterThan(root.get("createTime"), new Date(Long.parseLong(nt)));
            } else {
                //用户读取第一页时，领取系统消息
                messageService.receiveSystemMessage(frogUserDetails.getLoginUser(loginUserRepository));
            }
            messageList = userMessageRepository.findAll(Specification.where(loginUserCondition).and(createTimeCondition), PageRequest.of(0, limit, sort))
                    .stream().sorted((m1, m2) -> m2.getCreateTime().compareTo(m1.getCreateTime()))
                    .map(m -> addScore(m)).collect(Collectors.toList());

        }
        Date now = contestScheduleRepository.now();
        messageList.stream().forEach(messsage -> messageService.fill(messsage, now));
        return messageList;
    }

    private UserMessage addScore(UserMessage m) {
        m.setProperty("score", m.getCreateTime().getTime());
        LoginUser sourceLoginUser = m.getSourceLoginUser();
        if (sourceLoginUser != null) {
            m.setProperty("sourceLoginUserName", sourceLoginUser.getName());
            m.setProperty("sourceLoginUserAvatar", sourceLoginUser.getAvatar());
        }
        return m;
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "发布消息")
    //    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN')")
    @RequestMapping(value = "api/message/task/publish", method = RequestMethod.POST)
    MessageTask publish(@RequestParam("id") String id,
                        @DateTimeFormat(pattern = "yyyy-MM-dd'T'HH:mm")
                        @RequestParam(value = "publishTime", required = false) Date publishTime
    ) throws FrogException {
        return messageService.publish(id, publishTime);
    }

    private UserMessage addScore(SystemMessage m) {
        return addScore(messageService.systemMessage2userMessage(m));
    }
}

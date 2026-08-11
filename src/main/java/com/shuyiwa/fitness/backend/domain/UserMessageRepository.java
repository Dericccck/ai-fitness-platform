package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.List;
import java.util.Optional;

public interface UserMessageRepository extends PagingAndSortingRepository<UserMessage, String>, JpaSpecificationExecutor<UserMessage> {

    Optional<UserMessage> findTop1ByLoginUser_IdAndMessageTypeOrderByCreateTimeDesc(String loginUserId, UserMessage.MessageType messageType);

//    Optional<UserMessage> findBySystemMessageId(String systemMessageId);
//
//    List<UserMessage> findByLoginUser_IdAndCreateTimeGreaterThanOrderByCreateTimeAsc(String id, Date date, Pageable pageable);
//
//    List<UserMessage> findByLoginUser_IdOrderByCreateTimeDesc(String id, Pageable pageable);
//
//    List<UserMessage> findByLoginUser_IdAndCreateTimeLessThanOrderByCreateTimeDesc(String id, Date date, Pageable pageable);

    List<UserMessage> findByLoginUserAndMessageTask(LoginUser loginUser, MessageTask messageTask);

    void deleteByMessageTask_Id(String messageTaskId);

    List<UserMessage> findBySystemMessageIdAndLoginUser(String messageId, LoginUser loginUser);
}

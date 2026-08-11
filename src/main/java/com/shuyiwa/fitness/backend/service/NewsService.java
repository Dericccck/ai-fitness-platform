package com.shuyiwa.fitness.backend.service;

import com.alibaba.fastjson.JSON;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.backend.Utils;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.NewsType;
import com.shuyiwa.fitness.backend.event.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.hibernate.query.criteria.internal.CriteriaBuilderImpl;
import org.hibernate.query.criteria.internal.expression.LiteralExpression;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.math.BigDecimal;
import java.util.Calendar;
import java.util.Date;
import java.util.List;
import java.util.Optional;

import static com.shuyiwa.fitness.backend.domain.FeedItem.EntityType.ARTICLE;

@Service
public class NewsService {
    private static final Log logger = LogFactory.getLog(NewsService.class);

    @Autowired
    NewsRepository newsRepository;

    @Autowired
    NewsWechatRepository newsWechatRepository;

    @Autowired
    private ApplicationEventPublisher applicationEventPublisher;

    @Autowired
    AppointmentRepository appointmentRepository;

    @Autowired
    UserAndCoachRepository userAndCoachRepository;

    @Autowired
    LoginUserRepository loginUserRepository;


    @Transactional
    public void createNews(News news,LoginUser loginUser) throws FrogException {
        if(StringUtils.isEmpty(news.getEntityId())){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"消息参数不正确");
        }
       /* if(NewsType.changeClass.name().equals(news.getNewsType().name())
                || NewsType.changeCoach.name().equals(news.getNewsType().name())){
            if(StringUtils.isEmpty(news.getContent())){
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"参数必填");
            }
        }*/
//        news.setHandle_result(1);
        news.setCreateTime(new Date());
        news.setCreateLoginUser(loginUser);
        newsRepository.save(news);

        // 发送微信通知
        String content = "";
        if(NewsType.inviteUser.name().equals(news.getNewsType().name())) {
             content="您有一条健身邀约消息，请及时查看处理！";
        }else if(NewsType.appointments.name().equals(news.getNewsType().name())){
            content="您有一条约课消息，请及时查看处理！";
        }else if(NewsType.changeClass.name().equals(news.getNewsType().name())){
            content="您有一条改课消息，请及时查看处理！";
        }else if(NewsType.finishClass.name().equals(news.getNewsType().name())){
            content="您有一条核销课程消息，请及时查看处理！";
        }else if(NewsType.changeCoach.name().equals(news.getNewsType().name())){
            content="您有一条更换主教练的消息，请及时查看处理！";
        }/*else if(NewsType.unviteUser.name().equals(news.getNewsType().name())){
            content="我向您发起了解约申请，请及时处理！";
        }*/
        if(StringUtils.isEmpty(content))return;
        sendWechatNews(news,content);
    }

    @Transactional
    public void forceUPdateNews(int handleResult,String handleUserId,String newsType,String entityId){
        newsRepository.forceUPdateNews(handleResult,handleUserId,newsType,entityId);
    }

    @Transactional
    public void deleteNewsByEntityId(String entityId){
        newsRepository.deleteNewsByEntityId(entityId);
    }

    /**
     *
     * @param
     */
    @Transactional
    public void updateNews(String id,LoginUser loginUser,int status)throws FrogException{
        // todo:: 根据通知的类型，修改信息状态，发微信通知
        News news = newsRepository.findById(id).orElse(null);
        if(null==news){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"消息不存在");
        }
        if(0!=news.getHandle_result()){
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"本条目已由他人处理完成");
        }
        if(status==1){//确认
            news.setHandle_result(1);
        }else if(status==2){//拒绝
            news.setHandle_result(2);
        }
        news.setHandleTime(new Date());
        news.setHandleUserId(loginUser.getId());
        String content = "";
        if(NewsType.inviteUser.name().equals(news.getNewsType().name())){
            InviteUserEvent event = new InviteUserEvent();
            event.setEntityId(news.getEntityId());
            event.setStatus(status+"");
            event.setLoginUserId(loginUser.getId());
            event.setTime(new Date().getTime());
            applicationEventPublisher.publishEvent(event);


        }else if(NewsType.appointments.name().equals(news.getNewsType().name())){
            AppointmentHandleEvent event = new AppointmentHandleEvent();
            event.setEntityId(news.getEntityId());
            event.setStatus(status+"");
            event.setLoginUserId(loginUser.getId());
            event.setTime(new Date().getTime());
            event.setContent(news.getContent());

            applicationEventPublisher.publishEvent(event);

        }else if(NewsType.changeClass.name().equals(news.getNewsType().name())){
            ChangeClassHandleEvent event = new ChangeClassHandleEvent();
            event.setEntityId(news.getEntityId());
            event.setStatus(status+"");
            event.setContent(news.getContent());
//            if(status==2){
//                news.setContent(null);
//            }
            applicationEventPublisher.publishEvent(event);
            //todo::微信通知
//            content="我向您发起了改课申请，请及时处理！";

        }else if(NewsType.finishClass.name().equals(news.getNewsType().name())){
            FinishClassHandleEvent event = new FinishClassHandleEvent();
            event.setEntityId(news.getEntityId());
            event.setStatus(status+"");
            event.setLoginUserId(loginUser.getId());
            event.setTime(new Date().getTime());
            applicationEventPublisher.publishEvent(event);
            //todo::微信通知
//            content="我向您发起了结束课程申请，请及时处理！";
        }else if(NewsType.changeCoach.name().equals(news.getNewsType().name())){
            ChangeCoachEvent event = new ChangeCoachEvent();
            event.setEntityId(news.getEntityId());
            event.setStatus(status+"");
            event.setLoginUserId(loginUser.getId());
            event.setContent(news.getContent());
            applicationEventPublisher.publishEvent(event);
//            content="我向您发起了更换教练申请，请及时处理！";
            //todo::微信通知
        }else if(NewsType.unviteUser.name().equals(news.getNewsType().name())){
            UnviteUserEvent event = new UnviteUserEvent();
            event.setEntityId(news.getEntityId());
            event.setStatus(status+"");
            event.setLoginUserId(loginUser.getId());
            event.setContent(news.getContent());
            applicationEventPublisher.publishEvent(event);
            //
//            content="我向您发起了解约申请，请及时处理！";
        }

        newsRepository.updateNews(news.getHandle_result(),news.getId(),news.getVersion(),loginUser.getId());
//        sendWechatNews(news,content);
    }

    public void sendWechatNews(News news,String content){
        NewsWechatEvent newsWechatEvent = new NewsWechatEvent();
        newsWechatEvent.setLoginUser(news.getCreateLoginUser());
        newsWechatEvent.setWxOpenId(news.getReceiveLoginUser().getWeiXinOpenId());
        newsWechatEvent.setContent(content);
        newsWechatEvent.setSender(news.getCreateLoginUser().getName());
        applicationEventPublisher.publishEvent(newsWechatEvent);

    }

    public Page<News> findNewsByPage(int page,int size,String receiveUserId,String orgId){
        PageRequest pageRequest = PageRequest.of(page, size, Sort.by("createTime").descending());

        Specification<News> empty = Specification.where(null);
        Specification<News> notDeleted = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
        Specification<News> receiveUserCondition = StringUtils.isEmpty(receiveUserId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("receiveLoginUser").get("id"), receiveUserId);
        Specification<News> organizationCondition = StringUtils.isEmpty(orgId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), orgId);
        Specification<News>  newsTypeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.notEqual(root.get("newsType"), NewsType.changeCoach);
//        Specification<News> searchCondition = Optional.ofNullable(search).map(String::trim).map(v -> StringUtils.isEmpty(v) ? null : v).map(v -> (Specification<Certificate>) (root, query, criteriaBuilder) ->
//                criteriaBuilder.greaterThan(criteriaBuilder.function("match", Double.class, root.get("search"), new LiteralExpression<String>((CriteriaBuilderImpl) criteriaBuilder, Utils.injectSpace(v))), 0.)
//        ).orElse(empty);

        Page<News> pageResult = newsRepository.findAll(Specification
                        .where(notDeleted)
                        .and(receiveUserCondition)
                        .and(organizationCondition)
                        .and(newsTypeCondition)
                , pageRequest);

        pageResult.stream().forEach(news -> {
            if(news.getNewsType() == NewsType.finishClass || news.getNewsType() == NewsType.appointments){
               appointmentRepository.findById(news.getEntityId()).ifPresent(appointment -> {
                   appointment.getProperties().put("coachName",appointment.getCoach().getName());
                   appointment.getProperties().put("userName",appointment.getUser().getName());
                   news.setProperty("appointment",appointment);
               });
            }
            news.setProperty("handleUserName",StringUtils.isEmpty(news.getHandleUserId())?"":loginUserRepository.findById(news.getHandleUserId()).get().getName());
           /* if(!StringUtils.isEmpty(news.getContent())) {
                news.setProperty("contentJson", JSON.parseObject(news.getContent()));
            }*/
        });
        return pageResult;

    }

    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;

    public Page<News> findNewsByPageAdmin(int page,int size,String receiveUserId,String orgId,boolean all,boolean appointments,boolean finishClass,boolean changeClass,boolean unprocessed,boolean timeLimit){
        PageRequest pageRequest = PageRequest.of(page, size, Sort.by("createTime").descending());

        Specification<News> empty = Specification.where(null);
        Specification<News> notDeleted = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false);
//        Specification<News> receiveUserCondition = StringUtils.isEmpty(receiveUserId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("receiveLoginUser").get("id"), receiveUserId);
        Specification<News> organizationCondition = StringUtils.isEmpty(orgId) ? empty : (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("organization").get("id"), orgId);
//        Specification<News> searchCondition = Optional.ofNullable(search).map(String::trim).map(v -> StringUtils.isEmpty(v) ? null : v).map(v -> (Specification<Certificate>) (root, query, criteriaBuilder) ->
//                criteriaBuilder.greaterThan(criteriaBuilder.function("match", Double.class, root.get("search"), new LiteralExpression<String>((CriteriaBuilderImpl) criteriaBuilder, Utils.injectSpace(v))), 0.)
//        ).orElse(empty);
        Specification<News> appSearch = empty;
        Specification<News> finishSearch = empty;
        Specification<News> changeSearch = empty;
        Specification<News> unprocessedSearch = empty;
//        Specification<News> timeLimitSearch = empty;//即将过期

        if(!all){
            if(appointments){
                appSearch = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("newsType"), NewsType.appointments);
            }
            if(finishClass){
                finishSearch = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("newsType"), NewsType.finishClass);
            }
            if(changeClass){
                changeSearch = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("newsType"), NewsType.changeClass);
            }
        }

        if(unprocessed){
            unprocessedSearch = (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("handle_result"),0);
        }
        Specification<News>  newsTypeCondition = (root, query, criteriaBuilder) -> criteriaBuilder.notEqual(root.get("newsType"), NewsType.changeCoach);

        Page<News> pageResult = newsRepository.findAll(Specification
                        .where(notDeleted)
                        .and(organizationCondition)
                        .and(appSearch.or(finishSearch).or(changeSearch))
                        .and(unprocessedSearch)
                        .and(newsTypeCondition)
                , pageRequest);

        Date date = new Date();
        Calendar calendar = Calendar.getInstance();
        calendar.setTime(date);
        calendar.add(Calendar.HOUR, 1);
        Date afterOneHour = calendar.getTime();
        pageResult.stream().forEach(news -> {
            appointmentRepository.findById(news.getEntityId()).ifPresent(appointment -> {
                appointment.getProperties().put("coachName",appointment.getCoach().getName());
                appointment.getProperties().put("userName",appointment.getUser().getName());
                news.setProperty("appointment",appointment);

                //一小时内约课过期
                if(timeLimit){
                    if(appointment.getCourseStartTime().after(new Date()) && appointment.getCourseStartTime().before(afterOneHour)){
                        news.setProperty("timeLimit",1);
                    }
                }
            });

            news.setProperty("handleUserName",StringUtils.isEmpty(news.getHandleUserId())?"":loginUserRepository.findById(news.getHandleUserId()).get().getName());
            news.setProperty("receiveUserName",StringUtils.isEmpty(news.getReceiveLoginUser().getId())?"":loginUserRepository.findById(news.getReceiveLoginUser().getId()).get().getName());

            if(StringUtils.isEmpty(news.getCreateLoginUser().getId())){
                news.setProperty("createUserName","");
            }else{
                news.setProperty("createUserName",loginUserRepository.findById(news.getCreateLoginUser().getId()).get().getName());
                //List<LoginUserAuthority> authList = loginUserAuthorityRepository.findByLoginUserAndEntityId(news.getCreateLoginUser(),orgId);
                Integer authSize = loginUserAuthorityRepository.countByLoginUserAndEntityId(news.getCreateLoginUser(),orgId);
                if(authSize>0){
                    news.setProperty("createUserInTeam",1);
                }
            }

        });
        return pageResult;

    }

    public int countUnredNews(String userId,String organizationId){
       return newsRepository.countNewsByReceiveLoginUserAndHandleResult(userId,organizationId);
    }



}

package com.shuyiwa.fitness.backend.service;

import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.buffered.BatchBufferWorker;
import com.shuyiwa.fitness.backend.buffered.Bufferable;
import com.shuyiwa.fitness.backend.domain.NewsWechat;
import com.shuyiwa.fitness.backend.domain.NewsWechatRepository;
import com.shuyiwa.fitness.backend.event.NewsWechatEvent;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.third.weixin.service.ShareService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Date;
import java.util.List;

@Service
public class NewsWechatService {

    @Autowired
    NewsWechatRepository newsWechatRepository;

    @Autowired
    ShareService shareService;



    @EventListener
    @Transactional
    @Bufferable(permits = 0, name = "onNewsWechatEvent")
    public void onNewsWechatEvent(NewsWechatEvent event) {

    }

    @Transactional
    @BatchBufferWorker(name = "onNewsWechatEvent")
    public void onNewsWechatEvents(List<NewsWechatEvent> events) {
        for(NewsWechatEvent event:events){
            NewsWechat newsWechat =new NewsWechat();
            newsWechat.setContent(event.getContent());
            newsWechat.setLoginUserId(event.getLoginUser().getId());
            newsWechat.setCreateLoginUser(event.getLoginUser());
            newsWechat.setWeiXinOpenId(event.getWxOpenId());
            newsWechat.setUserName(event.getSender());
            newsWechat.setSendTime(new Date());
            newsWechat.setStatus(0);
            try {
                String sendResult = shareService.sendWeixinProgramMsg(event.getSender(),event.getContent(),event.getWxOpenId());
                newsWechat.setSendResult(sendResult);
                JSONObject result = JSONObject.parseObject(sendResult);
                if(0 == result.getInteger("errcode")){
                    newsWechat.setStatus(1);
                }else {
                    newsWechat.setStatus(-1);
                }
//                newsWechat.setStatus(1);
            } catch (FrogException e) {
                newsWechat.setSendResult(e.getMessage());
                newsWechat.setStatus(-1);
            }
            newsWechatRepository.save(newsWechat);
        }
    }

}

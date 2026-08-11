package com.shuyiwa.fitness.backend.third.weixin.service;

import com.alibaba.fastjson.JSONObject;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.backend.domain.LoginUser;
import com.shuyiwa.fitness.backend.domain.LoginUserRepository;
import com.shuyiwa.fitness.backend.domain.PhoneVerifyCode;
import com.shuyiwa.fitness.backend.domain.PhoneVerifyCodeRepository;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.service.LoginUserService;
import com.shuyiwa.fitness.backend.third.weixin.bean.ShareInfoBean;
import com.shuyiwa.fitness.backend.third.weixin.controller.WechatUtils;
import org.apache.commons.codec.digest.DigestUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;
import org.springframework.web.client.RestTemplate;

import javax.servlet.http.HttpServletRequest;
import java.io.UnsupportedEncodingException;
import java.net.URLDecoder;
import java.util.HashMap;
import java.util.Map;
import java.util.Random;

@Service
public class ShareService {
    private static final Log logger = LogFactory.getLog(ShareService.class);
    private static String weixinTicket = null;
    @Value("${com.shuyiwa.fitness.backend.weixin.app.id}")
    public String weixinAppId = null;
    @Value("${com.shuyiwa.fitness.backend.weixin.app.secret}")
    public String weixinSecretd = null;
    private String weixinGainTokenUrl = "https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=%s&secret=%s";
    private String weixinTicketUrl = "https://api.weixin.qq.com/cgi-bin/ticket/getticket?type=jsapi&access_token=";
    @Value("${com.shuyiwa.fitness.backend.weixin.app.share.noncestr:TQsdfpOBdfasdbnv}")
    private String weixinNoncestr = null;

    @Value("${com.shuyiwa.fitness.backend.weixin.minicode.id}")
    public String weixinminicodeId = null;

    @Value("${com.shuyiwa.fitness.backend.weixin.minicode.secret}")
    public String weixinminicodeSecretd = null;

    private String jscode2sessionUrl="https://api.weixin.qq.com/sns/jscode2session?appid=%s&secret=%s&js_code=%s&grant_type=authorization_code";

    private String sendWeixinMsg = "https://api.weixin.qq.com/cgi-bin/message/wxopen/template/uniform_send?access_token=%s";

    private  String getUserInfoUrl = "https://api.weixin.qq.com/cgi-bin/user/info?access_token=%s&openid=%s";

    private String sendWeixinsubMs = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token=%s";

    @Autowired
    PhoneVerifyCodeRepository phoneVerifyCodeRepository;

    @Autowired
    LoginUserService loginUserService;



    private static RestTemplate restTemplate = new RestTemplate();
    private static ObjectMapper mapper = new ObjectMapper();


    /**
     * 定时更新微信分享Ticket
     */
    public void weixinTokenGain() {
        String gainTokenUrl = String.format(weixinGainTokenUrl, weixinAppId, weixinSecretd);
        logger.info("gainTokenUrl:" + gainTokenUrl);
        try {
            Map<String, Object> accessTokenMap = restTemplate.getForObject(gainTokenUrl, HashMap.class);
            logger.info("accessTokenMap:" + mapper.writeValueAsString(accessTokenMap));
            Object accessTokenObject = accessTokenMap.get("access_token");
            if (accessTokenObject == null) {
                logger.error("weixinTokenGain,cannot get access_token");
            } else {
                String accessToken = accessTokenObject.toString();
                String weixinGainTicketUrl = weixinTicketUrl + accessToken;
                Map<String, Object> ticketJsonMap = restTemplate.getForObject(weixinGainTicketUrl, HashMap.class);
                logger.info("ticketJsonMap:" + mapper.writeValueAsString(ticketJsonMap));
                weixinTicket = ticketJsonMap.get("ticket").toString();
                logger.info("weixin accessToken[" + accessToken + "], ticket[" + weixinTicket + "].");
            }
        } catch (Exception e) {
            logger.error("weixinTokenGain", e);
        }
    }


    public String jscode2session(String jscode,String encryptedData,String iv) throws FrogException {
        if(StringUtils.isEmpty(jscode) || StringUtils.isEmpty(encryptedData) || StringUtils.isEmpty(iv)){
            throw new FrogException(500,"参赛不正确");
        }
        String code2sessionUrl = String.format(jscode2sessionUrl, weixinminicodeId,weixinminicodeSecretd,jscode);
        logger.info("code2sessionUrl:" + code2sessionUrl);
        try {
            String sessionKeyString = restTemplate.getForObject(code2sessionUrl, String.class);
            logger.info("sessionKeyString:" + sessionKeyString);
            JSONObject jsonObject = null;
            if(sessionKeyString!=null) {
                jsonObject = JSONObject.parseObject(sessionKeyString);
            }
            String session_key = "";
            String openid = "";
            String unionid = "";
            if(jsonObject!=null && jsonObject.containsKey("session_key")){
                 session_key = jsonObject.getString("session_key");
                 openid = jsonObject.getString("openid");
                logger.info("openId:"+openid);
                 unionid = jsonObject.getString("unionid");
                 logger.info("unionid:"+unionid);
            }else{
                throw new FrogException(500,"获取session失败");
            }
            if(StringUtils.isEmpty(openid)){
                throw new FrogException(500,"获取openId失败");
            }
            String result = WechatUtils.decryptWeChatData(session_key,iv, encryptedData);
            logger.info("encryptedData=="+result);
            JSONObject phoneObj = JSONObject.parseObject(result);
            String phone = phoneObj.getString("phoneNumber");

            getGetUserInfo(openid,getToken());
            createUser(phone,openid);
            return phone;
        }catch (Exception e){
            logger.error("jscode2session", e);
            throw new FrogException(500,e.getMessage());
        }
    }

    @Transactional
    public String getVercode(String phone,String clientIp){
        Random random = new Random();
        StringBuilder codeBuilder = new StringBuilder();
        for (int i = 0; i < 5; i++) {
            codeBuilder.append(random.nextInt(10));
        }
        String code = codeBuilder.toString();
        PhoneVerifyCode phoneVerifyCode = new PhoneVerifyCode();
        phoneVerifyCode.setCode(code);
        phoneVerifyCode.setDeleted(false);
        phoneVerifyCode.setIp(clientIp);
        phoneVerifyCode.setPhone(phone);
        phoneVerifyCodeRepository.save(phoneVerifyCode);
        return code;
    }


    public ShareInfoBean getWeixinShareInfo(HttpServletRequest req) throws UnsupportedEncodingException {
        if (weixinTicket == null) {
            weixinTokenGain();
        }
        String urlParam = "";
        urlParam = URLDecoder.decode(req.getParameter("url"), "utf-8");
//        urlParam = URLDecoder.decode(url,"utf-8");
        ShareInfoBean shareInfoBean = new ShareInfoBean();
        long now = System.currentTimeMillis();
        String nowStr = String.valueOf(now).substring(0, 10);
        String data = "jsapi_ticket=" + weixinTicket + "&noncestr=" + weixinNoncestr + "&timestamp=" + nowStr + "&url=" + urlParam;
//		logger.info("data: " + data + "----");
        String signature = DigestUtils.shaHex(data);
        shareInfoBean.setSignature(signature);
        shareInfoBean.setAppId(weixinAppId);
        shareInfoBean.setTimestamp(nowStr);
        shareInfoBean.setNonceStr(weixinNoncestr);

        return shareInfoBean;
    }

    //        获取用户微信信息
    public void getGetUserInfo(String openId,String accessToken) throws JsonProcessingException {
            String infourl = String.format(getUserInfoUrl,accessToken,openId);
            String r = restTemplate.getForObject(infourl,String.class);
            logger.info("weixinuserINfo: "+r);
    }


    public void sendWeixinMsg(){
        String gainTokenUrl = String.format(weixinGainTokenUrl, weixinAppId, weixinSecretd);
        logger.info("gainTokenUrl:" + gainTokenUrl);
        try {
            Map<String, Object> accessTokenMap = restTemplate.getForObject(gainTokenUrl, HashMap.class);
            logger.info("accessTokenMap:" + mapper.writeValueAsString(accessTokenMap));
            Object accessTokenObject = accessTokenMap.get("access_token");
            if (accessTokenObject == null) {
                logger.error("weixinTokenGain,cannot get access_token");
            } else {
                String accessToken = accessTokenObject.toString();
                //todo:::
            }
        }catch (Exception e){

        }
    }


    public static void main(String[] args) {
//        String accessToken = getToken();
//        sendMiniCodeMsg(accessToken);
//        try {
//            new ShareService().getGetUserInfo("oxeyr5BK31T29VF9sOrERz1hAoQY",accessToken);
//        } catch (JsonProcessingException e) {
//            e.printStackTrace();
//        }
//        sendMiniParamSubMsg(accessToken);

    }

    public String sendWeixinProgramMsg(String sender,String content,String wxopenId) throws FrogException {
        String accessToken = getToken();
        if(StringUtils.isEmpty(accessToken)){
           throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"请求微信小程序token异常");
        }
       return sendMiniParamSubMsg(accessToken,sender,content,wxopenId);

    }

    public  String  getToken(){
        //String weixintokenurl = "https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=%s&secret=%s";
        String TokenUrl = String.format(weixinGainTokenUrl, weixinminicodeId, weixinminicodeSecretd);
        logger.info("getTokenUrl:" + TokenUrl);
        try {
            RestTemplate restTemplate = new RestTemplate();
            Map<String, Object> accessTokenMap = restTemplate.getForObject(TokenUrl, HashMap.class);
            logger.info("accessTokenMap:" + mapper.writeValueAsString(accessTokenMap));
            Object accessTokenObject = accessTokenMap.get("access_token");
            if (accessTokenObject == null) {
                logger.error("getToken,cannot get access_token");
            } else {
                String accessToken = accessTokenObject.toString();
//                获取用户微信信息
//                String infourl = String.format(getUserInfoUrl,accessToken,"oxeyr5BK31T29VF9sOrERz1hAoQY");
//                String r = restTemplate.getForObject(infourl,String.class);
//                System.out.println(r);
//                System.out.println("----------------------------------");
                    return accessToken;

            }
        }catch (Exception e){
            e.printStackTrace();
        }
        return null;
    }

    /**
     * 发送小程序订阅类消息
     * @param accessToken
     */
    public String sendMiniParamSubMsg(String accessToken,String sender,String content,String wxopenId){

        String sendUrl = String.format(sendWeixinsubMs,accessToken);
        logger.info(sendUrl);
        JSONObject map = new JSONObject();
        map.put("touser",wxopenId);
        map.put("page","pages/index/index");
//        map.put("miniprogram_state","developer");
        map.put("lang","zh_CN");
        map.put("template_id","a7WZDluqFUxmJs5YLjs7EJd_pUaFoG9NetX2h0qeNqg");
        JSONObject datamap = new JSONObject();
        map.put("data",datamap);

        JSONObject name1=new JSONObject();
        JSONObject thing2=new JSONObject();

        name1.put("value",sender);
        thing2.put("value",content);

        datamap.put("name1",name1);
        datamap.put("thing2",thing2);

        logger.info(map.toJSONString());

        HttpHeaders headers = new HttpHeaders();
        headers.add("Content-Type","application/json;encoding=utf-8");
        HttpEntity<Map<String, Object>> httpEntity = new HttpEntity<>(map,headers);
        String s = restTemplate.postForObject(sendUrl,httpEntity,String.class);
        logger.info("result:"+s);
        return s;
    }

    /**
     * 发送小程序模板消息
     * @param accessToken
     */
    public static void sendMiniCodeMsg(String accessToken){

        String sendWeixinMs = "https://api.weixin.qq.com/cgi-bin/message/wxopen/template/uniform_send?access_token=%s";
        String sendUrl = String.format(sendWeixinMs,accessToken);
        System.out.println(sendUrl);
        JSONObject map = new JSONObject();
        map.put("touser","oxeyr5BK31T29VF9sOrERz1hAoQY");
        JSONObject mp_template_msg = new JSONObject();
        JSONObject miniprogram = new JSONObject();
        JSONObject data = new JSONObject();
        mp_template_msg.put("appid","wxb0efbb9546ea86e2");
        mp_template_msg.put("template_id","Esjw3y5s_bgOFmjSkmU4NVuP5gpDC0C0rIrOi7tHXx4");
        mp_template_msg.put("url","");

        miniprogram.put("appid","wx8491c19379bf3dbe");
//        miniprogram.put("pagepath","pages/index/index");
        mp_template_msg.put("miniprogram",miniprogram);

        JSONObject dmap = new JSONObject();
        dmap.put("value","健身预约提醒");
        dmap.put("color","#173177");
        data.put("first",dmap);

        dmap = new JSONObject();
        dmap.put("value","瑜伽");
        dmap.put("color","#173177");
        data.put("keyword1",dmap);

        dmap = new JSONObject();
        dmap.put("value","1人");
        dmap.put("color","#173177");
        data.put("keyword2",dmap);

        dmap = new JSONObject();
        dmap.put("value","2021-06-18 10:00 ~ 2021-06-18 11:00");
        dmap.put("color","#173177");
        data.put("keyword3",dmap);

        dmap=new JSONObject();
        dmap.put("value","请务必按时上课");
        dmap.put("color","#173177");
        data.put("remark",dmap);

        mp_template_msg.put("data",data);


        JSONObject weapp_template_msg = new JSONObject();
        weapp_template_msg.put("template_id","pjNn7RnKKcCVQzj_AAQg3FqlHDVf79PMQdt6o0D9bLk");
        //weapp_template_msg.put("page","pages/index/index");
        weapp_template_msg.put("form_id","");

        JSONObject minidata = new JSONObject();
        dmap = new JSONObject();
        dmap.put("value","吴先生");
//        dmap.put("color","#173177");
        minidata.put("name1",dmap);

        dmap = new JSONObject();
        dmap.put("value","2021-06-18");
//        dmap.put("color","#173177");
        minidata.put("date2",dmap);

        dmap = new JSONObject();
        dmap.put("value","瑜伽");
//        dmap.put("color","#173177");
        minidata.put("thing3",dmap);

        dmap = new JSONObject();
        dmap.put("value","2021-06-18 10:00 ~ 2021-06-18 11:00");
//        dmap.put("color","#173177");
        minidata.put("character_string5",dmap);

        dmap = new JSONObject();
        dmap.put("value","健身舒适会所");
//        dmap.put("color","#173177");
        minidata.put("thing7",dmap);

        weapp_template_msg.put("data",minidata);
        weapp_template_msg.put("emphasis_keyword","name1.DATA");

        //map.put("weapp_template_msg",weapp_template_msg);
        map.put("mp_template_msg",mp_template_msg);


        HttpHeaders headers = new HttpHeaders();
        headers.add("Content-Type","application/json;encoding=utf-8");
        HttpEntity<Map<String, Object>> httpEntity = new HttpEntity<>(map,headers);
        String s=restTemplate.postForObject(sendUrl,httpEntity,String.class);
        System.out.println(s);
    }



    /**
     * 发送公众号模板消息
     * @param accessToken
     */
    public void sendMobanMsg(String accessToken){
        String sendWeixinMs = "https://api.weixin.qq.com/cgi-bin/message/template/send?access_token=%s";
         JSONObject map = new JSONObject();
                map.put("touser","o8AEs5oZOVfic1nBI7cYFQUFg3jo");
                map.put("template_id","XteqTn0g757V2_hD03jkmQvJhY9s3nr89mOslj7fUM8");
                map.put("url","http://www.baidu.com");
                map.put("topcolor","#FF0000");

                JSONObject dataMap = new JSONObject();
                JSONObject dmap = new JSONObject();
                dmap.put("value","健身预约提醒");
                dmap.put("color","#173177");
                dataMap.put("first",dmap);

                dmap = new JSONObject();
                dmap.put("value","吴先生");
                dmap.put("color","#173177");
                dataMap.put("keyword1",dmap);

                dmap = new JSONObject();
                dmap.put("value","2021-06-17 10:00:00");
                dmap.put("color","#173177");
                dataMap.put("keyword2",dmap);

                dmap=new JSONObject();
                dmap.put("value","瑜伽");
                dmap.put("color","#173177");
                dataMap.put("keyword3",dmap);

                dmap=new JSONObject();
                dmap.put("value","2021-06-18 10:00 ~ 2021-06-18 11:00");
                dmap.put("color","#173177");
                dataMap.put("keyword4",dmap);

                dmap=new JSONObject();
                dmap.put("value","huyiwa健身huisuo");
                dmap.put("color","#173177");
                dataMap.put("keyword5",dmap);

                dmap=new JSONObject();
                dmap.put("value","预约健身课程");
                dmap.put("color","#173177");
                dataMap.put("remark",dmap);

                map.put("data",dataMap);

                System.out.println(map.toJSONString());
                HttpHeaders headers = new HttpHeaders();
                headers.add("Content-Type","application/json;encoding=utf-8");
                HttpEntity<Map<String, Object>> httpEntity = new HttpEntity<>(map,headers);
                String sendUrl = String.format(sendWeixinMs,accessToken);
                System.out.println(sendUrl);
                String s=restTemplate.postForObject(sendUrl,httpEntity,String.class);
                System.out.println(s);
    }
    @Autowired
    LoginUserRepository loginUserRepository;

    private void createUser(String phone,String wxOpenid){
        loginUserService.findAndCreateUserByWeiXinOpenId(phone,wxOpenid);
        /*LoginUser user = loginUserService.createLoginUser(phone,wxOpenid);
        if(StringUtils.isEmpty(user.getWeiXinOpenId())){
            user.setWeiXinOpenId(wxOpenid);
            loginUserRepository.save(user);
        }*/
    }




}

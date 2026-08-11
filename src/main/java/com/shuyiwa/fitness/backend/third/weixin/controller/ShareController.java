package com.shuyiwa.fitness.backend.third.weixin.controller;

import com.alibaba.fastjson.JSONObject;
import com.shuyiwa.fitness.backend.third.weixin.bean.ShareInfoBean;
import com.shuyiwa.fitness.backend.third.weixin.bean.Code2SessionBean;
import com.shuyiwa.fitness.backend.third.weixin.service.ShareService;
import com.shuyiwa.fitness.backend.util.WeiXinUtil;
import org.apache.commons.io.IOUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.*;
import sun.nio.ch.IOUtil;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.*;
import java.util.Arrays;

@Controller
public class ShareController {
    private static final Log logger = LogFactory.getLog(ShareController.class);

    @Autowired
    private ShareService shareService;


    @RequestMapping(value = "/api/weixin/shareInfo",method = RequestMethod.POST)
    @ResponseBody
    public ShareInfoBean getShareInfo(HttpServletRequest request) throws Exception {
        logger.info("call /api/weinxin/shareInfo");
        try {
            return shareService.getWeixinShareInfo(request);
        } catch (Exception e) {
            logger.info("weinxin/shareInfo !", e);
            throw e;
        }
    }

    /**
     * 微信小程序登录凭证校验,解密获取手机号
     * @param
     * @return
     * @throws Exception
     */
    @RequestMapping(value = "/api/weixin/auth/jscode2session",method = RequestMethod.POST)
    @ResponseBody
    public JSONObject minicodeAuthCode2Session(@RequestBody Code2SessionBean code2SessionBean,
                                               //@RequestParam(name = "jsCode") String jsCode,
                                               //@RequestParam(name ="encryptedData") String encryptedData,
                                               @RequestHeader("X-Forwarded-For") String[] xf,
                                               //@RequestParam(name = "iv") String iv,
                                               @RequestParam(value = "__ip",required = false) String clientIp) throws Exception {
        logger.info("call /api/weinxin/jscode2session: "+code2SessionBean.toString());
        try {
             String phone = shareService.jscode2session(code2SessionBean.getJsCode(),code2SessionBean.getEncryptedData(),code2SessionBean.getIv());
             clientIp = Arrays.stream(xf).reduce((first, second) -> second).orElse(clientIp);
             String code = shareService.getVercode(phone,clientIp);
             JSONObject result=new JSONObject();
             result.put("phone",phone);
             result.put("code",code);
             return result;
        } catch (Exception e) {
            logger.info("weinxin/jscode2session !", e);
            throw e;
        }
    }

    @RequestMapping(value = "/api/weixin/msg",method = {RequestMethod.POST,RequestMethod.GET})
    public void winxinMsgHandle(HttpServletRequest request, HttpServletResponse response,@RequestBody(required = false) String obj) {
        try {
            request.setCharacterEncoding("UTF-8");
        } catch (UnsupportedEncodingException e) {
            e.printStackTrace();
        }
        logger.info(request.getMethod());
        if(RequestMethod.GET.name().equals(request.getMethod())){
            response.setCharacterEncoding("UTF-8");
            String signature = request.getParameter("signature");//微信加密签名
            String timestamp = request.getParameter("timestamp");//时间戳
            String nonce = request.getParameter("nonce");//随机数
            String echostr = request.getParameter("echostr");//随机字符串
            logger.info("signature: "+ signature);
            logger.info("timestamp: "+ timestamp);
            logger.info("nonce: "+ nonce);
            logger.info("echostr: "+ echostr);
            PrintWriter out = null;
            //接入验证
            if (WeiXinUtil.checkSignature(signature, timestamp, nonce)) {
                if (echostr != null) {
                    logger.info("success echostr: "+echostr);
                    try {
                        out = response.getWriter();
                    } catch (IOException e) {
                        e.printStackTrace();
                    }
                    out.write(echostr);//验证成功返回的值
                    return;

                }
            }
        }else{
            logger.info("body:::"+obj);
        }

    }


}

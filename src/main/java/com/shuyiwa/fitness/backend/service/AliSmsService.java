package com.shuyiwa.fitness.backend.service;

import com.aliyuncs.CommonRequest;
import com.aliyuncs.CommonResponse;
import com.aliyuncs.DefaultAcsClient;
import com.aliyuncs.IAcsClient;
import com.aliyuncs.exceptions.ClientException;
import com.aliyuncs.exceptions.ServerException;
import com.aliyuncs.http.MethodType;
import com.aliyuncs.profile.DefaultProfile;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.backend.domain.SmsRepository;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.domain.Sms;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Condition;
import org.springframework.context.annotation.ConditionContext;
import org.springframework.context.annotation.Conditional;
import org.springframework.core.type.AnnotatedTypeMetadata;
import org.springframework.stereotype.Service;

import java.util.HashMap;

@Service
@Conditional(AliSmsService.class)
public class AliSmsService implements SmsService.Sp, Condition {
    private static final Log logger = LogFactory.getLog(AliSmsService.class);
    @Autowired
    SmsRepository smsRepository;
    @Value("${com.shuyiwa.fitness.backend.sms.ali.access-key-id}")
    String accessKeyId;
    @Value("${com.shuyiwa.fitness.backend.sms.ali.access-key-secret}")
    String accessKeySecret;
    @Autowired
    ObjectMapper mapper;

    @Override
    public boolean matches(ConditionContext context, AnnotatedTypeMetadata metadata) {
        return context.getEnvironment().getProperty("com.shuyiwa.fitness.backend.sms.platform", "").equalsIgnoreCase(Sms.Platform.Ali.name());
    }

    @Override
    public boolean send(Sms sms, HashMap<String, String> params, Sms.Template template) throws FrogException {
        String aliTemplateCode = template.getAliTemplateCode();
        String templateParam = null;
        String content = null;
        try {
            templateParam = mapper.writeValueAsString(params);
            content = mapper.writeValueAsString(new HashMap<String, Object>() {{
                put("params", params);
                put("aliTemplateCode", aliTemplateCode);
                put("desc", template.getContent());
            }});
        } catch (JsonProcessingException e) {
            logger.info("exception", e);
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "短信发送失败");
        }
        sms.setPlatform(Sms.Platform.Ali);
        sms.setContent(content);
        smsRepository.save(sms);


        DefaultProfile profile = DefaultProfile.getProfile("cn-hangzhou", accessKeyId, accessKeySecret);
        IAcsClient client = new DefaultAcsClient(profile);

        CommonRequest request = new CommonRequest();
        //request.setProtocol(ProtocolType.HTTPS);
        request.setMethod(MethodType.POST);
        request.setDomain("dysmsapi.aliyuncs.com");
        request.setVersion("2017-05-25");
        request.setAction("SendSms");
        request.putQueryParameter("RegionId", "cn-hangzhou");
        request.putQueryParameter("PhoneNumbers", sms.getPhone());
        request.putQueryParameter("SignName", "Fitooss");
        request.putQueryParameter("TemplateCode", aliTemplateCode);
        request.putQueryParameter("TemplateParam", templateParam);
        try {
            CommonResponse response = client.getCommonResponse(request);
            logger.info("sms response:" + response.getData());
            sms.setResponse(response.getData());
            if (response.getData().contains("\"Code\":\"OK\"")) {
                sms.setResult(Sms.SmsResult.SUCCESS);
            } else {
                sms.setResult(Sms.SmsResult.FAIL);
                return false;
            }
        } catch (ServerException e) {
            sms.setResult(Sms.SmsResult.EXCEPTION);
            throw new FrogException(FrogException.PHONE_VERIFY_CODE_SEND_Exception, "短信发送异常,phoneNumbers:" + sms.getPhone(), e);
        } catch (ClientException e) {
            sms.setResult(Sms.SmsResult.EXCEPTION);
            throw new FrogException(FrogException.PHONE_VERIFY_CODE_SEND_Exception, "短信发送异常,phoneNumbers:" + sms.getPhone(), e);
        } finally {
            smsRepository.save(sms);
        }

        return sms.getResult() == Sms.SmsResult.SUCCESS;
    }
}

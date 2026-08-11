package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.Sms;
import com.shuyiwa.fitness.backend.domain.SmsRepository;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.zx.sms.codec.cmpp.msg.CmppSubmitRequestMessage;
import com.zx.sms.codec.cmpp.msg.CmppSubmitResponseMessage;
import com.zx.sms.common.util.ChannelUtil;
import com.zx.sms.connect.manager.EndpointEntity;
import com.zx.sms.connect.manager.EndpointManager;
import com.zx.sms.connect.manager.cmpp.CMPPClientEndpointEntity;
import io.netty.util.ResourceLeakDetector;
import io.netty.util.concurrent.Promise;
import org.apache.commons.lang.StringUtils;
import org.apache.commons.lang.exception.ExceptionUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Condition;
import org.springframework.context.annotation.ConditionContext;
import org.springframework.context.annotation.Conditional;
import org.springframework.core.type.AnnotatedTypeMetadata;
import org.springframework.stereotype.Service;

import javax.annotation.PostConstruct;
import javax.annotation.PreDestroy;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.concurrent.TimeUnit;

import static com.shuyiwa.fitness.backend.global.FrogException.PHONE_VERIFY_CODE_SEND_Exception;

@Service
@Conditional(XunMaiSmsService.class)
public class XunMaiSmsService implements SmsService.Sp, Condition {
    private static final Log logger = LogFactory.getLog(XunMaiSmsService.class);

    @Value("${com.shuyiwa.fitness.backend.sms.xun-mai.password}")
    String password;
    @Value("${com.shuyiwa.fitness.backend.sms.xun-mai.src-id:106905839393}")
    String srcId;
    @Value("${com.shuyiwa.fitness.backend.sms.xun-mai.fee-type:01}")
    String feeType;
    @Value("${com.shuyiwa.fitness.backend.sms.xun-mai.fee-code:0}")
    String feeCode;
    @Value("${com.shuyiwa.fitness.backend.sms.xun-mai.service-id:10086}")
    String serviceId;
    @Autowired
    SmsRepository smsRepository;
    private CMPPClientEndpointEntity client;

    @PostConstruct
    void init() {
        ResourceLeakDetector.setLevel(ResourceLeakDetector.Level.ADVANCED);
        final EndpointManager manager = EndpointManager.INS;
        client = new CMPPClientEndpointEntity();
        client.setId("106993");
        client.setHost("120.26.65.203");
        client.setPort(7890);
        client.setChartset(Charset.forName("utf-8"));
        client.setUserName("106993");
        client.setPassword(password);

        client.setMaxChannels((short) 2);
        client.setVersion((short) 0x20);
        client.setRetryWaitTimeSec((short) 30);
        client.setUseSSL(false);
        client.setReSendFailMsg(false);
        client.setSupportLongmsg(EndpointEntity.SupportLongMessage.BOTH);
        client.setBusinessHandlerSet(new ArrayList<>());

        manager.addEndpointEntity(client);
        manager.openEndpoint(client);
    }

    @PreDestroy
    void destroy() {
        EndpointManager.INS.close();
    }

    @Override
    public boolean matches(ConditionContext context, AnnotatedTypeMetadata metadata) {
        return context.getEnvironment().getProperty("com.shuyiwa.fitness.backend.sms.platform", "").equalsIgnoreCase(Sms.Platform.XunMai.name());
    }

    @Override
    public boolean send(Sms sms, HashMap<String, String> params, Sms.Template template) throws FrogException {
        sms.setPlatform(Sms.Platform.XunMai);
        sms.setContent(template.getResolvedContent(params));
        smsRepository.save(sms);


        String resolvedContent = template.getResolvedContent(params, template.getXunMaiContent());
        if (resolvedContent == null) {
            sms.setResult(Sms.SmsResult.EXCEPTION);
            sms.setResponse("没有配置讯迈模版");
            smsRepository.save(sms);
        } else {
            CmppSubmitRequestMessage msg = new CmppSubmitRequestMessage();
            msg.setDestterminalId(sms.getPhone());
            msg.setSrcId(srcId);
            msg.setMsgContent(resolvedContent);
            msg.setFeeType(feeType);
            msg.setFeeCode(feeCode);
            msg.setServiceId(serviceId);
            msg.setRegisteredDelivery((short) 0);
            try {
                List<Promise> promiseList = null;
                promiseList = ChannelUtil.syncWriteLongMsgToEntity(client.getId(), msg);
                if (promiseList != null) {
                    for (Promise promise : promiseList) {
                        if (promise != null) {
                            promise.await(10, TimeUnit.SECONDS);
                            Object o = promise.getNow();
                            if (o instanceof CmppSubmitResponseMessage) {
                                long result = ((CmppSubmitResponseMessage) o).getResult();
                                if (result == 0L) {
                                    sms.setResult(Sms.SmsResult.SUCCESS);
                                } else {
                                    logger.info("fail:" + o);
                                    sms.setResult(Sms.SmsResult.FAIL);
                                }
                                sms.setResponse(o + "");
                            } else {
                                Throwable cause = promise.cause();
                                if (cause != null) {
                                    sms.setResult(Sms.SmsResult.EXCEPTION);
                                    setException(sms, cause);
                                    throw new FrogException(PHONE_VERIFY_CODE_SEND_Exception, "短信发送异常,phoneNumbers:" + sms.getPhone(), cause);
                                } else {
                                    logger.info("timeout");
                                    sms.setResult(Sms.SmsResult.FAIL);
                                    sms.setResponse("time out");
                                }
                            }
                            //这里不处理短信拆分为多条的情况，因为暂时不需要
                            break;
                        }
                    }
                }
                if (sms.getResult() == null) {
                    logger.info("promiseList:" + (promiseList == null ? null : promiseList.size()));
                    sms.setResult(Sms.SmsResult.FAIL);
                    sms.setResponse("not connect");
                }
            } catch (Exception e) {
                sms.setResult(Sms.SmsResult.EXCEPTION);
                setException(sms, e);
                throw new FrogException(PHONE_VERIFY_CODE_SEND_Exception, "短信发送异常,phoneNumbers:" + sms.getPhone(), e);
            } finally {
                smsRepository.save(sms);
            }
        }
        return sms.getResult() == Sms.SmsResult.SUCCESS;

    }

    private void setException(Sms sms, Throwable cause) {
        if (sms.getResponse() == null && cause != null) {
            String exception = ExceptionUtils.getStackTrace(cause);
            sms.setResponse(StringUtils.substring(exception, 0, 500));
        }
    }

}

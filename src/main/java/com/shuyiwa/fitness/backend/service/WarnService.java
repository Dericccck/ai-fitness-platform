package com.shuyiwa.fitness.backend.service;

import com.alibaba.fastjson.JSONObject;
import com.aliyun.openservices.cms.CMSClient;
import com.aliyun.openservices.cms.CMSClientInit;
import com.aliyun.openservices.cms.builder.metric.CustomMetricBuilder;
import com.aliyun.openservices.cms.metric.MetricAttribute;
import com.aliyun.openservices.cms.model.CustomMetric;
import com.aliyun.openservices.cms.request.CustomMetricUploadRequest;
import com.aliyun.openservices.cms.response.CustomMetricUploadResponse;
import net.javacrumbs.shedlock.support.Utils;
import org.apache.commons.lang.exception.ExceptionUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.env.Environment;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.web.context.request.ServletWebRequest;
import org.springframework.web.context.request.WebRequest;

import javax.annotation.PostConstruct;
import javax.servlet.http.HttpServletRequest;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;
import java.util.stream.Collectors;

@Service
public class WarnService {
    private static final Log logger = LogFactory.getLog(WarnService.class);
    @Autowired(required = false)
    public JavaMailSender emailSender;
    @Autowired
    Environment env;
    private CMSClient cmsClient;

    @Value("${aliyun.cms.endpoint:https://metrichub-cms-cn-hangzhou.aliyuncs.com}")
    private String cmsEndpoint;
    @Value("${aliyun.cms.accessKeyId:}")
    private String cmsAccessKeyId;
    @Value("${aliyun.cms.accessKeySecret:}")
    private String cmsAccessKeySecret;

    @PostConstruct
    void init() {
        CMSClientInit.groupId = 101L;//设置公共的应用组id
        //初始化client
        if (cmsAccessKeyId == null || cmsAccessKeyId.trim().isEmpty()
                || cmsAccessKeySecret == null || cmsAccessKeySecret.trim().isEmpty()) {
            logger.warn("Aliyun CMS credentials are not configured; monitoring upload is disabled");
            return;
        }
        cmsClient = new CMSClient(cmsEndpoint, cmsAccessKeyId, cmsAccessKeySecret);
    }

    public static String genId() {
        return new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX").format(new Date());
    }


    @Async
    public void warn(WebRequest webRequest, String title, Throwable e) {
        HashMap<String, String> dim = new HashMap<>();
        if (webRequest instanceof ServletWebRequest) {
            HttpServletRequest httpServletRequest = ((ServletWebRequest) webRequest).getRequest();
            dim.put("path", httpServletRequest.getServletPath());
            dim.put("method", httpServletRequest.getMethod());
        }
        warn(title, ExceptionUtils.getFullStackTrace(e), dim);
    }

    @Async
    public <E extends Throwable> void warnAndThrow(WarnService warnService, String title, E e) throws E {
        warnService.warn(title, e);
        throw e;
    }

    @Async
    public <E extends Throwable> void warnAndThrow(WarnService warnService, E e) throws E {
        warnService.warn(e.getMessage(), e);
        throw e;
    }

    @Async
    public void warn(String title, Throwable e) {
        warn(title, ExceptionUtils.getFullStackTrace(e), new HashMap<>());
    }


    @Async
    public void warn(String title, String body) {
        warn(title, body, new HashMap<>());
    }

    @Async
    public void warn(String title, String body, Map<String, String> dim) {
        logger.info("warn:" + title + ":" + body);
        title = Utils.getHostname() + ":" + title;
        try {
            if (emailSender != null && !isDev()) {
                logger.info("warn:send:" + title);

                SimpleMailMessage message = new SimpleMailMessage();
                message.setFrom("943102899@qq.com");
                message.setTo("943102899@qq.com");
                message.setSubject(title);
                message.setText(body);
                emailSender.send(message);
            }

        } catch (Throwable ex) {
            logger.warn("send warn mail failed", ex);
        }
        if (!isDev() && cmsClient != null) {
            try {
                CustomMetricBuilder customMetricBuilder = CustomMetric.builder()
                        .setMetricName("fitness-backend")//指标名
                        .setGroupId(9999L)//设置定制的分组id
                        .setTime(new Date())
                        .setType(CustomMetric.TYPE_VALUE)//类型为原始值
                        .appendValue(MetricAttribute.VALUE, 1f)
//                    .appendDimension("title", URLEncoder.encode(title, "utf-8"))//添加维度
                        ;
                dim.forEach((k, v) -> customMetricBuilder.appendDimension(k, v));

                CustomMetricUploadRequest request = CustomMetricUploadRequest.builder()
                        .append(customMetricBuilder//原始值，key只能为这个
                                .build())
                        .build();
                CustomMetricUploadResponse response = cmsClient.putCustomMetric(request);//上报
                logger.warn("end warn to monitor:" + JSONObject.toJSONString(response));
            } catch (Throwable ex) {
                logger.warn("send warn to monitor failed", ex);
            }
        }
    }

    private boolean isDev() {
        return Arrays.stream(env.getActiveProfiles()).collect(Collectors.toSet()).contains("dev");
    }


}

package com.shuyiwa.fitness.backend.third.aliyun.controller;

import com.shuyiwa.fitness.backend.third.aliyun.service.WorksVideoUploadService;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.*;

@Controller
@RequestMapping(value = "/api/aliyunCallback")
public class AliyunCallbackController {
    private static final Log logger = LogFactory.getLog(AliyunCallbackController.class);

    @Autowired
    private WorksVideoUploadService worksVideoUploadService;


    @RequestMapping(method = RequestMethod.POST)
    @ResponseBody
    public void callback(@RequestBody String requestBody, @RequestHeader("X-VOD-TIMESTAMP") String vodTimestamp, @RequestHeader("X-VOD-SIGNATURE") String vodSignature) throws Exception {
        logger.info("called /api/aliyunCallback");
        try {
            worksVideoUploadService.callback(requestBody, vodTimestamp, vodSignature);
        } catch (Exception e) {
            logger.info("aliyun callback !", e);
            throw e;
        }
    }

}

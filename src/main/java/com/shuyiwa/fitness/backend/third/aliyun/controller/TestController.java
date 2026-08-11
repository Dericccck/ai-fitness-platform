package com.shuyiwa.fitness.backend.third.aliyun.controller;

import com.shuyiwa.fitness.backend.third.aliyun.service.AliyunPicService;
import com.shuyiwa.fitness.backend.third.aliyun.service.AliyunVideoService;
import com.shuyiwa.fitness.backend.third.aliyun.service.WorksVideoUploadService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseBody;

import java.io.File;

@Controller
@RequestMapping("/aliyunTest")
public class TestController {
    @Autowired
    private AliyunVideoService aliyunVideoService;
    @Autowired
    private AliyunPicService aliyunPicService;

    @Autowired
    private WorksVideoUploadService worksVideoUploadService;

    @RequestMapping("/uploadvideo")
    @ResponseBody
    public String uploadVideo() {
        worksVideoUploadService.uploadVideoFile();
        return "ok";
    }

    @RequestMapping("/checkvideo")
    @ResponseBody
    public String checkVideo() {
        worksVideoUploadService.checkVideo();
        return "ok";
    }

    @RequestMapping("/uploadpic")
    @ResponseBody
    public String uploadPic() throws Exception {
        String filePath = "D:\\sayHello\\shuyiwa\\aaa.jpg";
        String url = aliyunPicService.uploadPic("sayHello", new File(filePath));
        return url;
    }


}

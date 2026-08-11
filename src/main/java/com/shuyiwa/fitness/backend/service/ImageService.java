package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.LoginUserFileRepository;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.third.aliyun.service.AliyunPicService;
import com.shuyiwa.fitness.backend.domain.LoginUserFile;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.io.File;

@Service
public class ImageService {
    private static final Log logger = LogFactory.getLog(ImageService.class);
    @Autowired
    LoginUserFileRepository loginUserFileRepository;
    @Autowired
    AliyunPicService aliyunPicService;

    /**
     * 如果是上传的文件生成的临时url，则将文件上传到阿里云，并返回url。其他情况url不动
     *
     * @param objectType
     * @param url
     * @return
     */
    public String upload(String objectType, String url) throws FrogException {
        logger.info("upload:objectType:" + objectType + ",url:" + url);
        if (url == null) {
            return null;
        }
        if (url.startsWith("https://console.fitooss.com/pass/disk")) {
            String filename = url.substring(url.lastIndexOf("/") + 1);
            int i = filename.indexOf(".");
            String id = i > 0 ? filename.substring(0, i) : filename;
            LoginUserFile loginUserFile = loginUserFileRepository.findById(id).orElse(null);
            if (loginUserFile != null) {
                File file = new File(loginUserFile.getPath());
                logger.info("file exists:" + loginUserFile.getPath());
                return aliyunPicService.uploadPic(objectType, file);
            }
        }
        return url;
    }
}

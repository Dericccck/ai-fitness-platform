package com.shuyiwa.fitness.backend.third.aliyun.service;

import com.aliyun.oss.OSS;
import com.aliyun.oss.OSSClientBuilder;
import com.shuyiwa.fitness.backend.global.FrogException;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.File;
import java.text.DateFormat;
import java.text.SimpleDateFormat;
import java.util.Date;

@Service
public class AliyunPicService {

    private static final Log logger = LogFactory.getLog(AliyunPicService.class);
    @Value("${aliyun.pic.accessKeyId:}")
    private String accessKeyId;
    @Value("${aliyun.pic.accessKeySecret:}")
    private String accessKeySecret;

    @Value("${aliyun.pic.endpoint:http://oss-cn-hangzhou.aliyuncs.com}")
    private String endpoint;
    @Value("${aliyun.pic.bucketName:fitooss}")
    private String bucketName = "fitooss";
    //    @Value("${aliyun.pic.picDomain:https://fitooss.oss-cn-hangzhou.aliyuncs.com}")
    @Value("${aliyun.pic.picDomain:https://img.fitooss.com}")
    private String picDomain;


    private DateFormat df = new SimpleDateFormat("yyyy/MM/dd");

    /**
     * 向OSS上传图片
     *
     * @param objectType 图片类型（头图、文章等）
     * @param picFile    图片文件
     * @return 图片上传后访问URL
     * @throws Exception
     */
    public String uploadPic(String objectType, File picFile) throws FrogException {

        if (picFile == null || !picFile.exists()) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "文件不存在！:" + picFile == null ? "null" : picFile.getPath());
        }

        String objectName = objectType + "/" + df.format(new Date()) + "/" + picFile.getName();
//       创建OSSClient实例。
//        OSSClient ossClient = new OSSClient(endpoint, accessKeyId, accessKeySecret);

        OSS ossClient = new OSSClientBuilder().build(endpoint, accessKeyId, accessKeySecret);
        try {
            // 上传文件。<yourLocalFile>由本地文件路径加文件名包括后缀组成，例如/users/local/myfile.txt。
            ossClient.putObject(bucketName, objectName, picFile);

            String url = picDomain + "/" + objectName + "?ts=" + System.currentTimeMillis();

            return url;
        } catch (Exception e) {
            logger.error("uploadPic", e);
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "阿里云上传失败");
        } finally {
            // 关闭OSSClient。
            ossClient.shutdown();
        }
    }
}

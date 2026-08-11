package com.shuyiwa.fitness.backend.conf;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.io.File;
import java.io.IOException;

@Configuration
public class WebMvcConfigurerAdapter implements WebMvcConfigurer {

    private static final Log logger = LogFactory.getLog(WebMvcConfigurerAdapter.class);

    @Value("${com.shuyiwa.fitness.backend.upload-dir:upload-dir}")
    String uploadDir;
    @Value("${com.shuyiwa.fitness.backend.works-dir:works-dir}")
    String worksDir;
    @Value("${com.shuyiwa.fitness.backend.user-avatar-dir:user-avatar-dir}")
    String userAvatarDir;
    @Value("${com.shuyiwa.fitness.backend.organization-logo-dir:organization-logo-dir}")
    String organizationLogoDir;
    @Value("${com.shuyiwa.fitness.backend.article-image-dir:article-image-dir}")
    String articleImageDir;
    @Value("${com.shuyiwa.fitness.backend.download-dir:download-dir}")
    String downloadDir;
    @Value("${com.shuyiwa.fitness.backend.app-store-dir:app-store-dir}")
    String appStoreDir;

    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        try {
            registry.addResourceHandler("disk/upload/**").addResourceLocations("file:" + new File(uploadDir).getCanonicalPath() + "/");
            registry.addResourceHandler("disk/works/**").addResourceLocations("file:" + new File(worksDir).getCanonicalPath() + "/");
            registry.addResourceHandler("disk/user/avatar/**").addResourceLocations("file:" + new File(userAvatarDir).getCanonicalPath() + "/");
            registry.addResourceHandler("disk/organization/logo/**").addResourceLocations("file:" + new File(organizationLogoDir).getCanonicalPath() + "/");
            registry.addResourceHandler("disk/article/image/**").addResourceLocations("file:" + new File(articleImageDir).getCanonicalPath() + "/");
            registry.addResourceHandler("disk/download/**").addResourceLocations("file:" + new File(downloadDir).getCanonicalPath() + "/");
            registry.addResourceHandler("disk/appStore/**").addResourceLocations("file:" + new File(appStoreDir).getCanonicalPath() + "/");
        } catch (IOException e) {
            logger.warn("exception when addResourceHandlers", e);
        }
    }
}

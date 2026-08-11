package com.shuyiwa.fitness.backend.third.aliyun.bean;

public class VideoUploadCompleteCBBean extends AliyunCBBean {

    private Long Size;
    private String FileUrl;

    public Long getSize() {
        return Size;
    }

    public void setSize(Long size) {
        Size = size;
    }

    public String getFileUrl() {
        return FileUrl;
    }

    public void setFileUrl(String fileUrl) {
        FileUrl = fileUrl;
    }
}

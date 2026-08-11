package com.shuyiwa.fitness.backend.third.aliyun.bean;

public class SnapshotCBBean extends AliyunCBBean {

    private String SubType;
    private String ErrorCode;
    private String ErrorMessage;
    private String CoverUrl;
    private SnapshotInfo[] SnapshotInfos;


    public String getSubType() {
        return SubType;
    }

    public void setSubType(String subType) {
        SubType = subType;
    }

    public String getErrorCode() {
        return ErrorCode;
    }

    public void setErrorCode(String errorCode) {
        ErrorCode = errorCode;
    }

    public String getErrorMessage() {
        return ErrorMessage;
    }

    public void setErrorMessage(String errorMessage) {
        ErrorMessage = errorMessage;
    }

    public String getCoverUrl() {
        return CoverUrl;
    }

    public void setCoverUrl(String coverUrl) {
        CoverUrl = coverUrl;
    }

    public SnapshotInfo[] getSnapshotInfos() {
        return SnapshotInfos;
    }

    public void setSnapshotInfos(SnapshotInfo[] snapshotInfos) {
        SnapshotInfos = snapshotInfos;
    }
}

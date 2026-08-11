package com.shuyiwa.fitness.backend.third.aliyun.bean;

import com.fasterxml.jackson.annotation.JsonProperty;

public class AliyunCBBean {
    //通用参数
    @JsonProperty(value = "EventTime")
    private String EventTime;

    @JsonProperty(value = "EventType")
    private String EventType;

    @JsonProperty(value = "VideoId")
    private String VideoId;

    @JsonProperty(value = "Status")
    private String Status;

    //视频上传完成
    @JsonProperty(value = "Size")
    private Long Size;
    @JsonProperty(value = "FileUrl")
    private String FileUrl;

    //视频转码完成
    @JsonProperty(value = "StreamInfos")
    private StreamInfo[] StreamInfos;

    //视频截图完成
    @JsonProperty(value = "SubType")
    private String SubType;
    @JsonProperty(value = "ErrorCode")
    private String ErrorCode;
    @JsonProperty(value = "ErrorMessage")
    private String ErrorMessage;
    @JsonProperty(value = "CoverUrl")
    private String CoverUrl;
    @JsonProperty(value = "SnapshotInfos")
    private SnapshotInfo[] SnapshotInfos;


    public String getEventTime() {
        return EventTime;
    }

    public void setEventTime(String eventTime) {
        EventTime = eventTime;
    }

    public String getEventType() {
        return EventType;
    }

    public void setEventType(String eventType) {
        EventType = eventType;
    }

    public String getVideoId() {
        return VideoId;
    }

    public void setVideoId(String videoId) {
        VideoId = videoId;
    }

    public String getStatus() {
        return Status;
    }

    public void setStatus(String status) {
        Status = status;
    }

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

    public StreamInfo[] getStreamInfos() {
        return StreamInfos;
    }

    public void setStreamInfos(StreamInfo[] streamInfos) {
        StreamInfos = streamInfos;
    }

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

package com.shuyiwa.fitness.backend.third.aliyun.bean;

import com.fasterxml.jackson.annotation.JsonProperty;

public class StreamInfo {
    @JsonProperty(value = "Status")
    private String Status;
    @JsonProperty(value = "Bitrate")
    private Float Bitrate;
    @JsonProperty(value = "Definition")
    private String Definition;
    @JsonProperty(value = "Duration")
    private Float Duration;
    @JsonProperty(value = "Encrypt")
    private Boolean Encrypt;
    @JsonProperty(value = "ErrorCode")
    private String ErrorCode;
    @JsonProperty(value = "ErrorMessage")
    private String ErrorMessage;
    @JsonProperty(value = "FileUrl")
    private String FileUrl;
    @JsonProperty(value = "Format")
    private String Format;
    @JsonProperty(value = "Fps")
    private Float Fps;
    @JsonProperty(value = "Height")
    private Long Height;
    @JsonProperty(value = "Size")
    private Long Size;
    @JsonProperty(value = "Width")
    private Long Width;

    public String getStatus() {
        return Status;
    }

    public void setStatus(String status) {
        Status = status;
    }

    public Float getBitrate() {
        return Bitrate;
    }

    public void setBitrate(Float bitrate) {
        Bitrate = bitrate;
    }

    public String getDefinition() {
        return Definition;
    }

    public void setDefinition(String definition) {
        Definition = definition;
    }

    public Float getDuration() {
        return Duration;
    }

    public void setDuration(Float duration) {
        Duration = duration;
    }

    public Boolean getEncrypt() {
        return Encrypt;
    }

    public void setEncrypt(Boolean encrypt) {
        Encrypt = encrypt;
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

    public String getFileUrl() {
        return FileUrl;
    }

    public void setFileUrl(String fileUrl) {
        FileUrl = fileUrl;
    }

    public String getFormat() {
        return Format;
    }

    public void setFormat(String format) {
        Format = format;
    }

    public Float getFps() {
        return Fps;
    }

    public void setFps(Float fps) {
        Fps = fps;
    }

    public Long getHeight() {
        return Height;
    }

    public void setHeight(Long height) {
        Height = height;
    }

    public Long getSize() {
        return Size;
    }

    public void setSize(Long size) {
        Size = size;
    }

    public Long getWidth() {
        return Width;
    }

    public void setWidth(Long width) {
        Width = width;
    }
}

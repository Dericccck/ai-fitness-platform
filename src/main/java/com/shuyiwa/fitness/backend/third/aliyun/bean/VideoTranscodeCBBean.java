package com.shuyiwa.fitness.backend.third.aliyun.bean;

public class VideoTranscodeCBBean extends AliyunCBBean {

    private StreamInfo[] StreamInfos;


    public StreamInfo[] getStreamInfos() {
        return StreamInfos;
    }

    public void setStreamInfos(StreamInfo[] streamInfos) {
        StreamInfos = streamInfos;
    }
}

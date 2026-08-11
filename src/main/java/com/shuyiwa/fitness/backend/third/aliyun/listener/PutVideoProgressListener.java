package com.shuyiwa.fitness.backend.third.aliyun.listener;

import com.aliyun.oss.event.ProgressEvent;
import com.aliyun.oss.event.ProgressEventType;
import com.aliyun.vod.upload.impl.VoDProgressListener;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;

public class PutVideoProgressListener implements VoDProgressListener {
    private static final Log logger = LogFactory.getLog(PutVideoProgressListener.class);
    private long bytesWritten = 0L;
    private long totalBytes = -1L;
    private boolean succeed = false;
    private String videoId;

    public PutVideoProgressListener() {
    }

    public void progressChanged(ProgressEvent progressEvent) {
        long bytes = progressEvent.getBytes();
        ProgressEventType eventType = progressEvent.getEventType();
        switch (eventType) {
            case TRANSFER_STARTED_EVENT:
                logger.info("Start to upload videoId " + this.videoId + "......");
                break;
            case REQUEST_CONTENT_LENGTH_EVENT:
                this.totalBytes = bytes;
                logger.info(this.totalBytes + "bytes in total will be uploaded to OSS.");
                break;
            case REQUEST_BYTE_TRANSFER_EVENT:
                this.bytesWritten += bytes;
                if (this.totalBytes != -1L) {
                    int percent = (int) ((double) this.bytesWritten * 100.0D / (double) this.totalBytes);
                    logger.info(this.videoId + "," + bytes + " bytes have been written at this time, upload progress: " + percent + "%(" + this.bytesWritten + "/" + this.totalBytes + ")");
                } else {
                    logger.info(this.videoId + "," + bytes + " bytes have been written at this time, upload sub total : (" + this.bytesWritten + ")");
                }
                break;
            case TRANSFER_COMPLETED_EVENT:
                this.succeed = true;
                logger.info("Succeed to upload videoId " + this.videoId + " , " + this.bytesWritten + " bytes have been transferred in total.");
                break;
            case TRANSFER_FAILED_EVENT:
                logger.info("Failed to upload videoId " + this.videoId + " , " + this.bytesWritten + " bytes have been transferred.");
        }

    }

    public boolean isSucceed() {
        return this.succeed;
    }

    public void onVidReady(String videoId) {
        this.setVideoId(videoId);
    }

    public String getVideoId() {
        return this.videoId;
    }

    public void setVideoId(String videoId) {
        this.videoId = videoId;
    }
}
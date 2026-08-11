package com.shuyiwa.fitness.backend.third.aliyun.bean;

import com.fasterxml.jackson.annotation.JsonProperty;

public class SnapshotInfo {
    @JsonProperty(value = "Status")
    private String Status;
    @JsonProperty(value = "SnapshotType")
    private String SnapshotType;
    @JsonProperty(value = "SnapshotCount")
    private Long SnapshotCount;
    @JsonProperty(value = "SnapshotFormat")
    private String SnapshotFormat;
    @JsonProperty(value = "SnapshotRegular")
    private String SnapshotRegular;
    @JsonProperty(value = "JobId")
    private String JobId;

    public String getStatus() {
        return Status;
    }

    public void setStatus(String status) {
        Status = status;
    }

    public String getSnapshotType() {
        return SnapshotType;
    }

    public void setSnapshotType(String snapshotType) {
        SnapshotType = snapshotType;
    }

    public Long getSnapshotCount() {
        return SnapshotCount;
    }

    public void setSnapshotCount(Long snapshotCount) {
        SnapshotCount = snapshotCount;
    }

    public String getSnapshotFormat() {
        return SnapshotFormat;
    }

    public void setSnapshotFormat(String snapshotFormat) {
        SnapshotFormat = snapshotFormat;
    }

    public String getSnapshotRegular() {
        return SnapshotRegular;
    }

    public void setSnapshotRegular(String snapshotRegular) {
        SnapshotRegular = snapshotRegular;
    }

    public String getJobId() {
        return JobId;
    }

    public void setJobId(String jobId) {
        JobId = jobId;
    }
}

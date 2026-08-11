//package com.shuyiwa.frog.core.task.domain;
//
//
//import org.hibernate.annotations.GenericGenerator;
//
//import javax.persistence.*;
//import java.util.Date;
//
///**
// * 任务
// */
//@Entity
//@Table(name = "task", uniqueConstraints = {
//        @UniqueConstraint(name = "uniqueTaskobj", columnNames = {"taskType", "objType", "objId"})
//})
//public class Task {
//    @Id
//    @Column(length = 32)
//    @GeneratedValue(generator = "system-uuid")
//    @GenericGenerator(name = "system-uuid", strategy = "uuid")
//    private String id;
//
//
//    @Column(length = 20)
//    private String taskType;
//
//    @Column
//    private String taskName;
//
//    @Column
//    private String executeHost;
//
//    @Temporal(TemporalType.TIMESTAMP)
//    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
//    private Date createTime;
//
//    @Column(length = 32)
//    private String objType;
//
//    @Column(length = 32)
//    private String objId;
//
//    @Column
//    private Date executeTimeBegin;
//
//    @Column
//    private Date executeTimeEnd;
//
//    @Column
//    private Integer executeTimes;
//
//    @Column
//    private Integer executeState;
//
//    public String getId() {
//        return id;
//    }
//
//    public void setId(String id) {
//        this.id = id;
//    }
//
//    public String getTaskType() {
//        return taskType;
//    }
//
//    public void setTaskType(String taskType) {
//        this.taskType = taskType;
//    }
//
//    public String getTaskName() {
//        return taskName;
//    }
//
//    public void setTaskName(String taskName) {
//        this.taskName = taskName;
//    }
//
//    public String getExecuteHost() {
//        return executeHost;
//    }
//
//    public void setExecuteHost(String executeHost) {
//        this.executeHost = executeHost;
//    }
//
//    public Date getCreateTime() {
//        return createTime;
//    }
//
//    public void setCreateTime(Date createTime) {
//        this.createTime = createTime;
//    }
//
//    public String getObjType() {
//        return objType;
//    }
//
//    public void setObjType(String objType) {
//        this.objType = objType;
//    }
//
//    public String getObjId() {
//        return objId;
//    }
//
//    public void setObjId(String objId) {
//        this.objId = objId;
//    }
//
//    public Date getExecuteTimeBegin() {
//        return executeTimeBegin;
//    }
//
//    public void setExecuteTimeBegin(Date executeTimeBegin) {
//        this.executeTimeBegin = executeTimeBegin;
//    }
//
//    public Date getExecuteTimeEnd() {
//        return executeTimeEnd;
//    }
//
//    public void setExecuteTimeEnd(Date executeTimeEnd) {
//        this.executeTimeEnd = executeTimeEnd;
//    }
//
//    public Integer getExecuteTimes() {
//        return executeTimes;
//    }
//
//    public void setExecuteTimes(Integer executeTimes) {
//        this.executeTimes = executeTimes;
//    }
//
//    public Integer getExecuteState() {
//        return executeState;
//    }
//
//    public void setExecuteState(Integer executeState) {
//        this.executeState = executeState;
//    }
//}

//package com.shuyiwa.frog.core.task.service;
//
//import com.shuyiwa.frog.core.task.domain.Task;
//import com.shuyiwa.frog.core.task.domain.TaskRepository;
//import com.shuyiwa.frog.core.task.domain.dict.ObjType;
//import com.shuyiwa.frog.core.task.domain.dict.TaskState;
//import com.shuyiwa.frog.core.task.domain.dict.TaskType;
//import org.springframework.beans.factory.annotation.Autowired;
//
//public class TaskService {
//
//    @Autowired
//    TaskRepository taskRepository;
//
//
//    public Task createTask(TaskType taskType, ObjType objType, String objId) {
//        Task task = new Task();
//        task.setTaskType(taskType.name());
//        task.setTaskName(taskType.getTaskName());
//        task.setObjType(objType.name());
//        task.setObjId(objId);
//        task.setExecuteTimes(0);
//        task.setExecuteState(TaskState.INIT.getStateCode());
//
//        task = taskRepository.save(task);
//
//        return task;
//    }
//
//
//}

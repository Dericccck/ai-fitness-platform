package com.shuyiwa.fitness.training.api;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Collections;
import java.util.Map;

/** 不访问数据库的存活探针，便于容器编排系统判断进程是否仍能响应。 */
@RestController
public class TrainingHealthController {

    @GetMapping("/health")
    public Map<String, String> health() {
        return Collections.singletonMap("status", "UP");
    }
}

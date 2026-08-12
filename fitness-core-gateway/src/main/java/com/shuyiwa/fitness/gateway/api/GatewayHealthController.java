package com.shuyiwa.fitness.gateway.api;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** 存活探针不访问数据库，只确认 Gateway 进程仍能响应。 */
@RestController
public class GatewayHealthController {

    @GetMapping("/health/live")
    public HealthView live() {
        return new HealthView("ok");
    }

    public static final class HealthView {
        private final String status;

        public HealthView(String status) {
            this.status = status;
        }

        public String getStatus() {
            return status;
        }
    }
}

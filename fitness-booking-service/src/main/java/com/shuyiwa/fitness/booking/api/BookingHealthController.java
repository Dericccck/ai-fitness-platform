package com.shuyiwa.fitness.booking.api;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 预约写服务的脱敏存活探针。
 *
 * <p>该接口只确认进程和 HTTP 端口可以响应，不访问 MySQL、RabbitMQ 或任何预约数据，
 * 避免健康检查产生锁、事务和业务副作用。数据库和消息组件是否可用由应用就绪检查
 * 以及真实联调前置检查分别验证。</p>
 */
@RestController
public class BookingHealthController {

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

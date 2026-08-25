package com.shuyiwa.fitness.customer.api;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 客服服务的进程存活探针。
 *
 * <p>该接口只确认 Spring Boot 进程可以响应，不访问 MySQL，也不代表客服工单业务已经
 * 就绪。数据库连接、内部 Token 和权限链路必须由后续的就绪检查或真实只读请求单独验证，
 * 避免把存活探针做成有副作用的业务接口。</p>
 */
@RestController
public class CustomerServiceHealthController {

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

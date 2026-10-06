package com.shuyiwa.fitness.training.api;

import java.util.Collections;
import java.util.Map;
import com.shuyiwa.fitness.training.config.RuntimeLifecycle;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** 平台通用就绪检查，使用只读 SQL，不输出连接配置或异常正文。 */
@RestController
public class RuntimeReadinessController {
    private final JdbcTemplate jdbc;
    private final RuntimeLifecycle lifecycle;
    public RuntimeReadinessController(JdbcTemplate jdbc, RuntimeLifecycle lifecycle) {
        this.jdbc = jdbc;
        this.lifecycle = lifecycle;
    }
    @GetMapping("/health/runtime-ready")
    public ResponseEntity<Map<String, String>> ready() {
        boolean ready = !lifecycle.isDraining();
        if (ready) {
            try { ready = Integer.valueOf(1).equals(jdbc.queryForObject("SELECT 1", Integer.class)); }
            catch (RuntimeException unavailable) { ready = false; }
        }
        return ResponseEntity.status(ready ? 200 : 503)
                .body(Collections.singletonMap("status", ready ? "ready" : "not_ready"));
    }
}

package com.shuyiwa.fitness.training.config;

import java.util.concurrent.Executor;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.apache.catalina.connector.Connector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.embedded.tomcat.TomcatServletWebServerFactory;
import org.springframework.boot.web.server.WebServerFactoryCustomizer;
import org.springframework.context.ApplicationListener;
import org.springframework.context.event.ContextClosedEvent;
import org.springframework.core.Ordered;
import org.springframework.stereotype.Component;

/** Spring Boot 2.1 的显式 Tomcat drain，先停止接流量再销毁数据库等依赖。 */
@Component
public class RuntimeLifecycle implements WebServerFactoryCustomizer<TomcatServletWebServerFactory>,
        ApplicationListener<ContextClosedEvent>, Ordered {
    private static final Logger LOG = LoggerFactory.getLogger(RuntimeLifecycle.class);
    private final AtomicBoolean draining = new AtomicBoolean(false);
    private volatile Connector connector;
    @Value("${fitness.shutdown.timeout-seconds:${FITNESS_SHUTDOWN_TIMEOUT_SECONDS:45}}")
    private long timeoutSeconds;

    @Override
    public void customize(TomcatServletWebServerFactory factory) {
        factory.addConnectorCustomizers(value -> connector = value);
    }

    public boolean isDraining() { return draining.get(); }

    @Override
    public int getOrder() { return Ordered.HIGHEST_PRECEDENCE; }

    @Override
    public void onApplicationEvent(ContextClosedEvent event) {
        if (!draining.compareAndSet(false, true) || connector == null) return;
        connector.pause();
        Executor executor = connector.getProtocolHandler().getExecutor();
        if (executor instanceof ThreadPoolExecutor) {
            ThreadPoolExecutor pool = (ThreadPoolExecutor) executor;
            pool.shutdown();
            try {
                if (!pool.awaitTermination(Math.max(1, Math.min(120, timeoutSeconds)), TimeUnit.SECONDS)) {
                    LOG.warn("Graceful shutdown deadline exceeded; durable business recovery remains required");
                }
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
        }
    }
}

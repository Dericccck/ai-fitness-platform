package com.shuyiwa.fitness.booking.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Booking Outbox 发布器配置。
 *
 * <p>发布器默认关闭，避免开发者尚未启动 RabbitMQ 时预约服务不断连接外部消息系统。
 * 生产环境开启后，只有 RabbitMQ publisher confirm 返回 ack，事件才会被标记为 PUBLISHED。</p>
 */
@ConfigurationProperties(prefix = "booking.outbox")
public class BookingOutboxProperties {
    private boolean publisherEnabled;
    private long fixedDelayMs = 1000;
    private int batchSize = 20;
    private int maxAttempts = 8;
    private int claimLeaseSeconds = 60;
    private int retryBaseSeconds = 5;
    private long confirmTimeoutMs = 5000;
    private String exchange = "fitness.booking.events";
    private String queue = "fitness.booking.events";
    private String routingKey = "appointment.created";

    public boolean isPublisherEnabled() { return publisherEnabled; }
    public void setPublisherEnabled(boolean publisherEnabled) { this.publisherEnabled = publisherEnabled; }
    public long getFixedDelayMs() { return fixedDelayMs; }
    public void setFixedDelayMs(long fixedDelayMs) { this.fixedDelayMs = fixedDelayMs; }
    public int getBatchSize() { return batchSize; }
    public void setBatchSize(int batchSize) { this.batchSize = batchSize; }
    public int getMaxAttempts() { return maxAttempts; }
    public void setMaxAttempts(int maxAttempts) { this.maxAttempts = maxAttempts; }
    public int getClaimLeaseSeconds() { return claimLeaseSeconds; }
    public void setClaimLeaseSeconds(int claimLeaseSeconds) { this.claimLeaseSeconds = claimLeaseSeconds; }
    public int getRetryBaseSeconds() { return retryBaseSeconds; }
    public void setRetryBaseSeconds(int retryBaseSeconds) { this.retryBaseSeconds = retryBaseSeconds; }
    public long getConfirmTimeoutMs() { return confirmTimeoutMs; }
    public void setConfirmTimeoutMs(long confirmTimeoutMs) { this.confirmTimeoutMs = confirmTimeoutMs; }
    public String getExchange() { return exchange; }
    public void setExchange(String exchange) { this.exchange = exchange; }
    public String getQueue() { return queue; }
    public void setQueue(String queue) { this.queue = queue; }
    public String getRoutingKey() { return routingKey; }
    public void setRoutingKey(String routingKey) { this.routingKey = routingKey; }
}

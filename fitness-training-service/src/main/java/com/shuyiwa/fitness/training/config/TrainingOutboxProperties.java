package com.shuyiwa.fitness.training.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 训练计划事件 Outbox 配置；默认关闭发布，避免本地服务未准备 RabbitMQ 时丢失业务写入。 */
@ConfigurationProperties(prefix = "training.outbox")
public class TrainingOutboxProperties {
    private boolean publisherEnabled;
    private long fixedDelayMs = 1000L;
    private int batchSize = 20;
    private int maxAttempts = 8;
    private int claimLeaseSeconds = 60;
    private int retryBaseSeconds = 5;
    private int confirmTimeoutMs = 5000;
    private String exchange = "fitness.domain.events";
    private String routingKey = "training.plan.published";

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
    public int getConfirmTimeoutMs() { return confirmTimeoutMs; }
    public void setConfirmTimeoutMs(int confirmTimeoutMs) { this.confirmTimeoutMs = confirmTimeoutMs; }
    public String getExchange() { return exchange; }
    public void setExchange(String exchange) { this.exchange = exchange; }
    public String getRoutingKey() { return routingKey; }
    public void setRoutingKey(String routingKey) { this.routingKey = routingKey; }
}

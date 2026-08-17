package com.shuyiwa.fitness.gateway.service;

import com.shuyiwa.fitness.gateway.api.OperationsViews;
import com.shuyiwa.fitness.gateway.operations.OperationsMetric;
import com.shuyiwa.fitness.gateway.operations.OperationsMetricRow;
import com.shuyiwa.fitness.gateway.operations.OperationsTimeBucket;
import com.shuyiwa.fitness.gateway.repository.OperationsReadRepository;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.List;
import java.util.stream.Collectors;

/**
 * Operations Agent 查询编排和权限边界。
 *
 * <p>经营指标属于机构级敏感数据，只允许系统管理员和机构管理员访问。教练和学员即使
 * 知道 organizationId，也不能通过更换 URL 参数读取经营汇总。时间范围限制为 92 天，
 * 防止模型一次调用拉取过大的历史聚合。</p>
 */
@Service
@Transactional(readOnly = true)
public class OperationsToolService {

    private static final ZoneId BUSINESS_ZONE = ZoneId.of("Asia/Shanghai");
    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;
    private final OperationsReadRepository repository;

    public OperationsToolService(OperationsReadRepository repository) {
        this.repository = repository;
    }

    /** 保留 v1 调用方式；未指定时间桶时继续返回整个区间汇总。 */
    public OperationsViews.MetricView metric(
            AgentContext context,
            String organizationId,
            String metricCode,
            LocalDate from,
            LocalDate to,
            Integer requestedLimit
    ) {
        return metric(context, organizationId, metricCode, from, to, requestedLimit, null);
    }

    public OperationsViews.MetricView metric(
            AgentContext context,
            String organizationId,
            String metricCode,
            LocalDate from,
            LocalDate to,
            Integer requestedLimit,
            String bucketCode
    ) {
        requireOperationsRole(context);
        if (organizationId == null || organizationId.trim().isEmpty()
                || !context.canAccessOrganization(organizationId)) {
            throw new GatewayForbiddenException("organization is outside operations context scope");
        }

        LocalDate end = to == null ? LocalDate.now(BUSINESS_ZONE) : to;
        LocalDate start = from == null ? end.minusDays(30) : from;
        if (start.isAfter(end) || end.toEpochDay() - start.toEpochDay() > 92) {
            throw new IllegalArgumentException("operations time range must be 0 to 92 days");
        }
        OperationsMetric metric = OperationsMetric.parse(metricCode);
        OperationsTimeBucket bucket = OperationsTimeBucket.parse(bucketCode);
        if (bucket != OperationsTimeBucket.NONE && !supportsTimeBucket(metric)) {
            throw new IllegalArgumentException(
                    "DAY/WEEK time buckets currently support appointment, completed class, course and coach metrics");
        }
        int limit = normalizeLimit(requestedLimit);
        Instant fromInstant = start.atStartOfDay(BUSINESS_ZONE).toInstant();
        Instant toInstant = end.plusDays(1).atStartOfDay(BUSINESS_ZONE).toInstant();
        List<OperationsMetricRow> rawRows =
                bucket == OperationsTimeBucket.NONE
                        ? repository.query(organizationId, metric, fromInstant, toInstant, limit)
                        : repository.query(organizationId, metric, bucket, fromInstant, toInstant, limit);
        List<OperationsViews.MetricRowView> rows = rawRows.stream()
                .map(OperationsViews.MetricRowView::new)
                .collect(Collectors.toList());
        return new OperationsViews.MetricView(
                metric.getCode(), bucket.getCode(), organizationId, start, end, rows, Instant.now()
        );
    }

    private void requireOperationsRole(AgentContext context) {
        if (!context.isSystemAdmin() && !context.isOrganizationAdmin()) {
            throw new GatewayForbiddenException("operations metrics require administrator role");
        }
    }

    /**
     * 时间桶仍然只开放给固定的预约类指标；状态分布和剩余课时不允许被伪装成趋势。
     * 该白名单与 Repository 的固定 SQL 分支保持一致，避免只改一层造成权限或口径漂移。
     */
    private boolean supportsTimeBucket(OperationsMetric metric) {
        return metric == OperationsMetric.APPOINTMENT_COUNT
                || metric == OperationsMetric.COMPLETED_CLASS_COUNT
                || metric == OperationsMetric.COURSE_APPOINTMENT_COUNT
                || metric == OperationsMetric.COACH_APPOINTMENT_COUNT;
    }

    private int normalizeLimit(Integer requestedLimit) {
        if (requestedLimit == null) {
            return DEFAULT_LIMIT;
        }
        if (requestedLimit < 1 || requestedLimit > MAX_LIMIT) {
            throw new IllegalArgumentException("limit must be between 1 and 100");
        }
        return requestedLimit;
    }
}

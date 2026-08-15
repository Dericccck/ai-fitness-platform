package com.shuyiwa.fitness.gateway.repository;

import com.shuyiwa.fitness.gateway.operations.OperationsMetric;
import com.shuyiwa.fitness.gateway.operations.OperationsMetricRow;
import com.shuyiwa.fitness.gateway.operations.OperationsTimeBucket;

import java.time.Instant;
import java.util.List;

/** Operations Agent 的只读数据端口，业务层不依赖具体 SQL。 */
public interface OperationsReadRepository {

    List<OperationsMetricRow> query(
            String organizationId,
            OperationsMetric metric,
            Instant from,
            Instant to,
            int limit
    );

    /**
     * 按受控时间桶查询。默认实现只允许 NONE 复用旧的区间汇总，避免新增指标时绕过
     * 固定 SQL 和数据范围评审。
     */
    default List<OperationsMetricRow> query(
            String organizationId,
            OperationsMetric metric,
            OperationsTimeBucket bucket,
            Instant from,
            Instant to,
            int limit
    ) {
        if (bucket == OperationsTimeBucket.NONE) {
            return query(organizationId, metric, from, to, limit);
        }
        throw new IllegalArgumentException("time bucket is not supported by this repository");
    }
}

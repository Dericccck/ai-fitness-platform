package com.shuyiwa.fitness.gateway.repository;

import com.shuyiwa.fitness.gateway.operations.OperationsMetric;
import com.shuyiwa.fitness.gateway.operations.OperationsMetricRow;

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
}

package com.shuyiwa.fitness.gateway.api;

import com.shuyiwa.fitness.gateway.operations.OperationsMetricRow;

import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Operations Agent 对外暴露的稳定只读视图。 */
public final class OperationsViews {

    private OperationsViews() {
    }

    public static final class MetricView {
        private final String metric;
        private final String organizationId;
        private final LocalDate from;
        private final LocalDate to;
        private final List<MetricRowView> rows;
        private final Instant generatedAt;

        public MetricView(String metric, String organizationId, LocalDate from, LocalDate to,
                          List<MetricRowView> rows, Instant generatedAt) {
            this.metric = metric;
            this.organizationId = organizationId;
            this.from = from;
            this.to = to;
            this.rows = Collections.unmodifiableList(new ArrayList<>(rows));
            this.generatedAt = generatedAt;
        }

        public String getMetric() { return metric; }
        public String getOrganizationId() { return organizationId; }
        public LocalDate getFrom() { return from; }
        public LocalDate getTo() { return to; }
        public List<MetricRowView> getRows() { return rows; }
        public Instant getGeneratedAt() { return generatedAt; }
    }

    public static final class MetricRowView {
        private final String dimension;
        private final String label;
        private final long value;

        public MetricRowView(OperationsMetricRow row) {
            this.dimension = row.getDimension();
            this.label = row.getLabel();
            this.value = row.getValue();
        }

        public String getDimension() { return dimension; }
        public String getLabel() { return label; }
        public long getValue() { return value; }
    }
}

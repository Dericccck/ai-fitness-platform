package com.shuyiwa.fitness.gateway.operations;

/** 经营指标的一行聚合结果；只返回报表所需字段，不返回旧业务表整行数据。 */
public final class OperationsMetricRow {

    private final String dimension;
    private final String label;
    private final long value;

    public OperationsMetricRow(String dimension, String label, long value) {
        this.dimension = dimension;
        this.label = label;
        this.value = value;
    }

    public String getDimension() {
        return dimension;
    }

    public String getLabel() {
        return label;
    }

    public long getValue() {
        return value;
    }
}

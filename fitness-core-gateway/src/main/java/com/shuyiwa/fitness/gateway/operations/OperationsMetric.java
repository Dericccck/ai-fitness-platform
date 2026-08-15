package com.shuyiwa.fitness.gateway.operations;

/**
 * Operations Agent 第一阶段允许查询的经营指标目录。
 *
 * <p>指标 ID 是跨 Python Agent、Java Gateway 和前端报表的稳定契约。这里刻意不接受
 * 任意 SQL 或任意表名：每增加一个指标，都必须经过代码评审，明确数据范围、脱敏方式
 * 和组织过滤条件。</p>
 */
public enum OperationsMetric {
    APPOINTMENT_COUNT("APPOINTMENT_COUNT", "预约总量"),
    APPOINTMENT_STATUS_BREAKDOWN("APPOINTMENT_STATUS_BREAKDOWN", "预约状态分布"),
    COURSE_APPOINTMENT_COUNT("COURSE_APPOINTMENT_COUNT", "课程预约量"),
    COACH_APPOINTMENT_COUNT("COACH_APPOINTMENT_COUNT", "教练预约量"),
    REMAINING_CLASS_HOURS("REMAINING_CLASS_HOURS", "课程剩余课时");

    private final String code;
    private final String description;

    OperationsMetric(String code, String description) {
        this.code = code;
        this.description = description;
    }

    public String getCode() {
        return code;
    }

    public String getDescription() {
        return description;
    }

    public static OperationsMetric parse(String value) {
        for (OperationsMetric metric : values()) {
            if (metric.code.equals(value)) {
                return metric;
            }
        }
        throw new IllegalArgumentException("unsupported operations metric");
    }
}

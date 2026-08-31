package com.shuyiwa.fitness.gateway.operations;

/**
 * Operations Agent 允许的时间分组粒度。
 *
 * <p>时间桶是固定契约，不接受模型传入任意 SQL 表达式。NONE 保持原来的整个区间
 * 汇总；DAY 和 WEEK 目前只开放给预约总量、课程预约量和教练预约量，用于趋势计算。</p>
 */
public enum OperationsTimeBucket {
    NONE("NONE"),
    DAY("DAY"),
    WEEK("WEEK");

    private final String code;

    OperationsTimeBucket(String code) {
        this.code = code;
    }

    public String getCode() {
        return code;
    }

    public static OperationsTimeBucket parse(String value) {
        if (value == null || value.trim().isEmpty()) {
            return NONE;
        }
        for (OperationsTimeBucket bucket : values()) {
            if (bucket.code.equalsIgnoreCase(value)) {
                return bucket;
            }
        }
        throw new IllegalArgumentException("不支持的经营时间桶");
    }
}

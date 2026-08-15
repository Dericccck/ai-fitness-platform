package com.shuyiwa.fitness.booking.domain;

/**
 * 旧 appointment.status 的业务编码。
 *
 * <p>本次写服务只创建 {@link #APPOINTMENT_SUCCESS}。其他状态由旧的教练核销和
 * 预约处理流程维护，不能被 Agent 创建接口随意指定。</p>
 */
public final class AppointmentStatusCodes {
    private AppointmentStatusCodes() {}

    public static final int APPOINTING = 0;             // 预约中
    public static final int APPOINTMENT_SUCCESS = 1;   // 预约成功
    public static final int APPOINTMENT_FAIL = 2;      // 预约失败
    public static final int APPOINTMENT_CHANGING = 3;  // 改课中
    public static final int WAITING_FOR_FINISH = 4;    // 待核销
    public static final int FINISHING = 5;              // 核销中
    public static final int FINISH_SUCCESS = 6;        // 已核销
    public static final int FINISH_FAIL = 7;            // 核销失败
}

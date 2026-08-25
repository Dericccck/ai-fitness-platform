package com.shuyiwa.fitness.customer.api;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

/** 存活探针只验证进程响应，不连接数据库或创建客服工单。 */
public class CustomerServiceHealthControllerTest {

    @Test
    public void liveProbeReturnsStableOkStatus() {
        CustomerServiceHealthController.HealthView result =
                new CustomerServiceHealthController().live();

        assertEquals("ok", result.getStatus());
    }
}

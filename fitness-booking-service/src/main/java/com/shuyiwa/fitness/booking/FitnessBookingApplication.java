package com.shuyiwa.fitness.booking;

import com.shuyiwa.fitness.booking.config.BookingProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

/** 预约业务写服务启动入口，独立于只读 Gateway 和历史 Java Entity 图。 */
@SpringBootApplication
@EnableConfigurationProperties(BookingProperties.class)
public class FitnessBookingApplication {

    public static void main(String[] args) {
        SpringApplication.run(FitnessBookingApplication.class, args);
    }
}

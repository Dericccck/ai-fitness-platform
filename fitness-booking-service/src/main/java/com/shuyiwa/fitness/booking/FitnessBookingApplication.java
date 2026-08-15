package com.shuyiwa.fitness.booking;

import com.shuyiwa.fitness.booking.config.BookingProperties;
import com.shuyiwa.fitness.booking.config.BookingOutboxProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

/** 预约业务写服务启动入口，独立于只读 Gateway 和历史 Java Entity 图。 */
@SpringBootApplication
@EnableConfigurationProperties({BookingProperties.class, BookingOutboxProperties.class})
@EnableScheduling
public class FitnessBookingApplication {

    public static void main(String[] args) {
        SpringApplication.run(FitnessBookingApplication.class, args);
    }
}

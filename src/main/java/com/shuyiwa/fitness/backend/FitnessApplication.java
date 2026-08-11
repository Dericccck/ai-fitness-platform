package com.shuyiwa.fitness.backend;

import com.shuyiwa.fitness.backend.buffered.EnableBuffering;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
//import org.springframework.cloud.config.server.EnableConfigServer;
import org.springframework.cloud.netflix.eureka.server.EnableEurekaServer;
import org.springframework.session.jdbc.config.annotation.web.http.EnableJdbcHttpSession;

@SpringBootApplication
@EnableEurekaServer
//@EnableConfigServer
@EnableBuffering
@EnableJdbcHttpSession(maxInactiveIntervalInSeconds = 86400*7)
public class FitnessApplication {
    public static void main(String[] args) {
            SpringApplication.run(FitnessApplication.class, args);
    }
}

package com.shuyiwa.fitness.customer.config;

import com.shuyiwa.fitness.customer.security.CustomerServiceActorArgumentResolver;
import com.shuyiwa.fitness.customer.security.CustomerServiceSecurityInterceptor;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.List;

/** 只保护客服内部 API，健康检查不需要业务主体。 */
@Configuration
public class CustomerServiceWebConfiguration implements WebMvcConfigurer {

    private final CustomerServiceProperties properties;

    public CustomerServiceWebConfiguration(CustomerServiceProperties properties) {
        this.properties = properties;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new CustomerServiceSecurityInterceptor(properties))
                .addPathPatterns("/internal/customer-service/**");
    }

    @Override
    public void addArgumentResolvers(List<HandlerMethodArgumentResolver> resolvers) {
        resolvers.add(new CustomerServiceActorArgumentResolver());
    }
}

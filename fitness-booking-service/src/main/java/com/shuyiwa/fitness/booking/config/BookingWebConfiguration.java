package com.shuyiwa.fitness.booking.config;

import com.shuyiwa.fitness.booking.security.BookingActorArgumentResolver;
import com.shuyiwa.fitness.booking.security.BookingSecurityInterceptor;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.List;

/** 注册预约服务的内部认证拦截器和主体参数解析器。 */
@Configuration
public class BookingWebConfiguration implements WebMvcConfigurer {
    private final BookingProperties properties;

    public BookingWebConfiguration(BookingProperties properties) { this.properties = properties; }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new BookingSecurityInterceptor(properties))
                .addPathPatterns("/internal/booking/**");
    }

    @Override
    public void addArgumentResolvers(List<HandlerMethodArgumentResolver> resolvers) {
        resolvers.add(new BookingActorArgumentResolver());
    }
}

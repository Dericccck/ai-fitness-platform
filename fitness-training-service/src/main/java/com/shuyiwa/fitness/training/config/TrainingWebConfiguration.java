package com.shuyiwa.fitness.training.config;

import com.shuyiwa.fitness.training.security.TrainingSecurityInterceptor;
import com.shuyiwa.fitness.training.security.TrainingActorArgumentResolver;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.List;

/** 只保护内部训练 API，健康检查等基础接口不经过业务主体拦截器。 */
@Configuration
public class TrainingWebConfiguration implements WebMvcConfigurer {

    private final TrainingProperties properties;

    public TrainingWebConfiguration(TrainingProperties properties) {
        this.properties = properties;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new TrainingSecurityInterceptor(properties))
                .addPathPatterns("/internal/training/**");
    }

    @Override
    public void addArgumentResolvers(List<HandlerMethodArgumentResolver> resolvers) {
        resolvers.add(new TrainingActorArgumentResolver());
    }
}

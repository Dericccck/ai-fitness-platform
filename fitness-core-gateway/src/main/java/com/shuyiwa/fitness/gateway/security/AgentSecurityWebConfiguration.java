package com.shuyiwa.fitness.gateway.security;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.List;

/** 注册 Gateway 的统一认证拦截器和上下文参数解析器。 */
@Configuration
public class AgentSecurityWebConfiguration implements WebMvcConfigurer {

    private final AgentContextInterceptor interceptor;

    public AgentSecurityWebConfiguration(
            InternalServiceTokenVerifier internalTokenVerifier,
            AgentContextVerifier contextVerifier
    ) {
        this.interceptor = new AgentContextInterceptor(internalTokenVerifier, contextVerifier);
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(interceptor).addPathPatterns("/internal/agent-tools/**");
    }

    @Override
    public void addArgumentResolvers(List<HandlerMethodArgumentResolver> resolvers) {
        resolvers.add(new AgentContextArgumentResolver());
    }
}

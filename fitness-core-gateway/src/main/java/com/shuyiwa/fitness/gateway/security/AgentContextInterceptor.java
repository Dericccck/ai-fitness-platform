package com.shuyiwa.fitness.gateway.security;

import org.springframework.web.servlet.HandlerInterceptor;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

/** 在所有 Agent Tool 入口统一执行双层认证。 */
public class AgentContextInterceptor implements HandlerInterceptor {

    public static final String CONTEXT_REQUEST_ATTRIBUTE = AgentContext.class.getName();
    private static final String INTERNAL_TOKEN_HEADER = "X-Internal-Service-Token";
    private static final String AGENT_CONTEXT_HEADER = "X-Agent-Context";

    private final InternalServiceTokenVerifier internalTokenVerifier;
    private final AgentContextVerifier contextVerifier;

    public AgentContextInterceptor(
            InternalServiceTokenVerifier internalTokenVerifier,
            AgentContextVerifier contextVerifier
    ) {
        this.internalTokenVerifier = internalTokenVerifier;
        this.contextVerifier = contextVerifier;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        internalTokenVerifier.verify(request.getHeader(INTERNAL_TOKEN_HEADER));
        AgentContext context = contextVerifier.verify(request.getHeader(AGENT_CONTEXT_HEADER));
        request.setAttribute(CONTEXT_REQUEST_ATTRIBUTE, context);
        return true;
    }
}

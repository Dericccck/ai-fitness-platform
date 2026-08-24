package com.shuyiwa.fitness.customer.security;

import org.springframework.core.MethodParameter;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;

/** 把已认证的客服主体注入 Controller，避免 Controller 直接读取 Header。 */
public class CustomerServiceActorArgumentResolver implements HandlerMethodArgumentResolver {

    @Override
    public boolean supportsParameter(MethodParameter parameter) {
        return CustomerServiceActor.class.equals(parameter.getParameterType());
    }

    @Override
    public Object resolveArgument(MethodParameter parameter, ModelAndViewContainer container,
                                  NativeWebRequest request, WebDataBinderFactory binderFactory) {
        return request.getAttribute(CustomerServiceSecurityInterceptor.ACTOR_ATTRIBUTE,
                NativeWebRequest.SCOPE_REQUEST);
    }
}

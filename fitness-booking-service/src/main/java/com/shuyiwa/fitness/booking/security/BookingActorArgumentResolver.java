package com.shuyiwa.fitness.booking.security;

import org.springframework.core.MethodParameter;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;

/** 把内部拦截器生成的主体注入 Controller，不让 Controller 直接读取 Header。 */
public class BookingActorArgumentResolver implements HandlerMethodArgumentResolver {
    @Override
    public boolean supportsParameter(MethodParameter parameter) {
        return BookingActor.class.equals(parameter.getParameterType());
    }

    @Override
    public Object resolveArgument(MethodParameter parameter, ModelAndViewContainer container,
                                  NativeWebRequest request, WebDataBinderFactory binderFactory) {
        return request.getAttribute(BookingSecurityInterceptor.ACTOR_ATTRIBUTE,
                NativeWebRequest.SCOPE_REQUEST);
    }
}

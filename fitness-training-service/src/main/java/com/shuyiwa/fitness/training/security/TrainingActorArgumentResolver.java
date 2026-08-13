package com.shuyiwa.fitness.training.security;

import org.springframework.core.MethodParameter;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;

/** 将已通过内部认证的 TrainingActor 注入 Controller，避免 Controller 读取原始 Header。 */
public class TrainingActorArgumentResolver implements HandlerMethodArgumentResolver {

    @Override
    public boolean supportsParameter(MethodParameter parameter) {
        return TrainingActor.class.equals(parameter.getParameterType());
    }

    @Override
    public Object resolveArgument(MethodParameter parameter, ModelAndViewContainer container,
                                  NativeWebRequest request, WebDataBinderFactory binderFactory) {
        return request.getAttribute(TrainingSecurityInterceptor.ACTOR_ATTRIBUTE,
                NativeWebRequest.SCOPE_REQUEST);
    }
}

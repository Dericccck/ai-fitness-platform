package com.shuyiwa.fitness.backend.conf;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.MethodParameter;
import org.springframework.util.StreamUtils;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import javax.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import java.nio.charset.Charset;
import java.util.List;


@Configuration
public class RequestBodyPartResolverConfig implements WebMvcConfigurer {

    @Autowired
    private ObjectMapper mapper;

    @Override
    public void addArgumentResolvers(List<HandlerMethodArgumentResolver> resolvers) {
        resolvers.add(new RequestBodyPartResolver());
    }

    @Target(ElementType.PARAMETER)
    @Retention(RetentionPolicy.RUNTIME)
    public @interface RequestBodyPart {
        String value();
    }

    public class RequestBodyPartResolver implements HandlerMethodArgumentResolver {
        private static final String JSONBODYATTRIBUTE = "JSON_REQUEST_BODY";

        @Override
        public boolean supportsParameter(MethodParameter parameter) {
            return parameter.hasParameterAnnotation(RequestBodyPart.class);
        }

        @Override
        public Object resolveArgument(MethodParameter parameter, ModelAndViewContainer mavContainer, NativeWebRequest webRequest, WebDataBinderFactory binderFactory) throws Exception {
            String body = getRequestBody(webRequest);
            if ("".equals(body)) {
                return null;
            }
            JsonNode jsonNode = mapper.readTree(body);
            if (jsonNode == null) {
                return null;
            }
            JsonNode partNode = jsonNode.get(parameter.getParameterAnnotation(RequestBodyPart.class).value());
            if (partNode == null) {
                return null;
            }
            return mapper.treeToValue(partNode, parameter.getParameterType());
        }

        private String getRequestBody(NativeWebRequest webRequest) {
            HttpServletRequest servletRequest = webRequest.getNativeRequest(HttpServletRequest.class);
            String jsonBody = (String) servletRequest.getAttribute(JSONBODYATTRIBUTE);
            if (jsonBody == null) {
                try {
                    String body = StreamUtils.copyToString(servletRequest.getInputStream(), Charset.forName("utf-8"));
                    servletRequest.setAttribute(JSONBODYATTRIBUTE, body);
                    return body;
                } catch (IOException e) {
                    throw new RuntimeException(e);
                }
            }
            return jsonBody;

        }

    }

}

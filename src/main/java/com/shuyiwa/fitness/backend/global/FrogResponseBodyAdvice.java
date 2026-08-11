package com.shuyiwa.fitness.backend.global;

import com.shuyiwa.fitness.backend.domain.RestResponse;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.core.MethodParameter;
import org.springframework.http.MediaType;
import org.springframework.http.converter.HttpMessageConverter;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.http.server.ServletServerHttpRequest;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.servlet.mvc.method.annotation.ResponseBodyAdvice;

import javax.servlet.http.HttpServletRequest;
import java.util.Collections;
import java.util.HashSet;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

@ControllerAdvice
public class FrogResponseBodyAdvice implements ResponseBodyAdvice<Object> {
    private static final Log logger = LogFactory.getLog(FrogResponseBodyAdvice.class);

    @Override
    public boolean supports(MethodParameter returnType, Class<? extends HttpMessageConverter<?>> converterType) {
        return true;
    }

    @Override
    public Object beforeBodyWrite(Object body, MethodParameter returnType, MediaType selectedContentType, Class<? extends HttpMessageConverter<?>> selectedConverterType, ServerHttpRequest request, ServerHttpResponse response) {
        String path = null;
        if (request instanceof ServletServerHttpRequest) {
            HttpServletRequest servletRequest = ((ServletServerHttpRequest) request).getServletRequest();
            path = servletRequest.getServletPath();
            if (path == null || (!path.startsWith("api") && !path.startsWith("/api"))) {
                return body;
            }

            if (path.startsWith("/api/img/log") || path.startsWith("/api/pay/wx/notify")) {
                return body;
            }
        }

        RestResponse output;
        if (body instanceof RestResponse) {
            output = (RestResponse) body;
        } else {
            output = new RestResponse<>();
            output.setCode(200);
            output.setData(body);
        }
        Set<String> debugUrls = new HashSet<>();
        debugUrls.add("/api/health/check");
        debugUrls.add("/api/sec/checkAuth");

        if (request instanceof ServletServerHttpRequest) {
            HttpServletRequest servletRequest = ((ServletServerHttpRequest) request).getServletRequest();
            String method = request.getMethod().name();
            String ip = Optional.ofNullable(servletRequest.getParameter("__ip")).orElse("unknown");
            String xfs = Optional.ofNullable(servletRequest.getHeaders("X-Forwarded-For"))
                    .map(xf -> Collections.list(xf).stream().collect(Collectors.joining(",")))
                    .orElse("null");
            if (debugUrls.contains(servletRequest.getServletPath())) {
                logger.debug("request:" + method + ":" + path + ":" + ip + ":" + xfs);
            } else {
                logger.info("request:" + method + ":" + path + ":" + ip + ":" + xfs);
            }
        }
        return output;
    }

}

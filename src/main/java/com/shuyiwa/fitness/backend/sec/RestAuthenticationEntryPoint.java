package com.shuyiwa.fitness.backend.sec;

import com.shuyiwa.fitness.backend.global.FrogException;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.stereotype.Component;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;

@Component
public class RestAuthenticationEntryPoint implements AuthenticationEntryPoint {
    private static final Log logger = LogFactory.getLog(RestAuthenticationEntryPoint.class);

    @Override
    public void commence(
            final HttpServletRequest request,
            final HttpServletResponse response,
            final AuthenticationException authException) throws IOException {
        response.setHeader("Content-Type", "application/json;charset=UTF-8");
        response.getWriter().write("{\"code\":" + FrogException.UNAUTHORIZED + ",\"message\":\"auth failed\",\"data\":false}");
        logger.info("RestAuthenticationEntryPoint commence:" + FrogException.UNAUTHORIZED);
    }
}

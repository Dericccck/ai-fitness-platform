package com.shuyiwa.fitness.backend.sec;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.authentication.AuthenticationServiceException;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.util.StringUtils;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Map;

public class FrogAuthenticationFilter extends UsernamePasswordAuthenticationFilter {
    public static final String SPRING_SECURITY_FORM_CHANNEL_KEY = "channel";
    public static final String SPRING_SECURITY_FORM_PHONE_KEY = "phone";
    public static final String SPRING_SECURITY_FORM_PHONE_VERIFY_CODE_KEY = "code";

    @Autowired
    ObjectMapper objectMapper;

    @Override
    public Authentication attemptAuthentication(HttpServletRequest request, HttpServletResponse response) throws AuthenticationException {
        if (!request.getMethod().equals("POST")) {
            throw new AuthenticationServiceException(
                    "Authentication method not supported: " + request.getMethod());
        }

        String phone = obtainStringDefaultEmpty(request, SPRING_SECURITY_FORM_PHONE_KEY);
        String code = obtainStringDefaultEmpty(request, SPRING_SECURITY_FORM_PHONE_VERIFY_CODE_KEY);
        String channel = obtainStringDefaultEmpty(request, SPRING_SECURITY_FORM_CHANNEL_KEY);
        if (StringUtils.isEmpty(phone) && StringUtils.isEmpty(code) && StringUtils.isEmpty(channel) && "application/json".equals(request.getHeader("Content-Type"))) {
            try {
                Map<String, String> form = objectMapper.readValue(request.getInputStream(), Map.class);
                if (form != null) {
                    phone = form.getOrDefault(SPRING_SECURITY_FORM_PHONE_KEY, "");
                    code = form.getOrDefault(SPRING_SECURITY_FORM_PHONE_VERIFY_CODE_KEY, "");
                    channel = form.getOrDefault(SPRING_SECURITY_FORM_CHANNEL_KEY, "");
                }
            } catch (IOException e) {
                logger.warn("login form invalidate", e);
            }
        }

        FrogAuthenticationToken authRequest = new FrogAuthenticationToken(phone, code, channel);

        // Allow subclasses to set the "details" property
        setDetails(request, authRequest);

        return this.getAuthenticationManager().authenticate(authRequest);
    }

    protected String obtainStringDefaultEmpty(HttpServletRequest request, String parameter) {
        String value = request.getParameter(parameter);
        if (value == null) {
            value = "";
        }
        value = value.trim();
        return value;
    }

}

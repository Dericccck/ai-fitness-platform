package com.shuyiwa.fitness.backend.global;

import com.shuyiwa.fitness.backend.Utils;
import com.shuyiwa.fitness.backend.service.WarnService;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;

import javax.servlet.*;
import javax.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.util.Optional;

@Component
public class LogFilter implements Filter {
    private static final Log logger = LogFactory.getLog(LogFilter.class);
    @Autowired
    WarnService warnService;

    @Override
    public void init(FilterConfig filterConfig) throws ServletException {

    }

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain) throws IOException, ServletException {
        long start = System.currentTimeMillis();
        String name = Thread.currentThread().getName();
        String servletPath = "";
        try {
            if (request instanceof HttpServletRequest) {
                servletPath = ((HttpServletRequest) request).getServletPath();
                Thread.currentThread().setName(name + ":" + servletPath);
                try {
                    if (servletPath.contains("disk/works")) {
                        logger.info("diskInfo:disk/works:" + Utils.requestInfo((HttpServletRequest) request));
                    }
                    if (servletPath.contains("disk/works")) {
                        logger.info("diskInfo:disk/upload:" + Utils.requestInfo((HttpServletRequest) request));
                    }
                } catch (Exception e) {
                    logger.info("diskInfo:exception", e);

                }
            }
            chain.doFilter(request, response);
        } finally {
            long cost = System.currentTimeMillis() - start;
            String phone = Optional.ofNullable(SecurityContextHolder.getContext().getAuthentication())
                    .map(authentication -> authentication.getPrincipal())
                    .filter(principal -> principal instanceof FrogUserDetails)
                    .map(frogUserDetails -> ((FrogUserDetails) frogUserDetails))
                    .map(FrogUserDetails::getPhone).orElse("null");
            SecurityContextHolder.getContext().getAuthentication();
            logger.info("doFilter:task:" + servletPath + ":phone:" + phone + ":cost:" + cost);
            if (cost > 550) {
                if (servletPath.startsWith("/api/health/check")) {
                    warnService.warn("doFilter:task:" + servletPath + ":phone:" + phone + ":cost:" + cost, "doFilter:task:" + servletPath + ":cost:" + cost);
                }
            }
            Thread.currentThread().setName(name);
        }
    }

    @Override
    public void destroy() {

    }
}

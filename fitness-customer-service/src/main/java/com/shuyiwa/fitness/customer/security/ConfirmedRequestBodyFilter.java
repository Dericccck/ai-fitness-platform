package com.shuyiwa.fitness.customer.security;

import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import javax.servlet.*;
import javax.servlet.http.*;
import java.io.*;
import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;

/** 仅适用于 Gateway 的受认证内部链路；摘要不是替代服务 Token 的授权凭证。 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 20)
public class ConfirmedRequestBodyFilter extends OncePerRequestFilter {
    @Override protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
            FilterChain chain) throws ServletException, IOException {
        if (!"POST".equals(request.getMethod()) || !request.getRequestURI().startsWith("/internal/")
                || request.getHeader("X-Confirmation-Id") == null) {
            chain.doFilter(request, response); return;
        }
        String expected = request.getHeader("X-Confirmed-Body-SHA256");
        if (expected == null || !expected.matches("[0-9a-f]{64}")) { response.sendError(401); return; }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        ServletInputStream original = request.getInputStream();
        while ((count = original.read(buffer)) != -1) {
            if (out.size() + count > 2 * 1024 * 1024) { response.sendError(413); return; }
            out.write(buffer, 0, count);
        }
        byte[] body = out.toByteArray();
        String actual;
        try {
            StringBuilder hex = new StringBuilder(64);
            for (byte value : MessageDigest.getInstance("SHA-256").digest(body))
                hex.append(String.format("%02x", value & 0xff));
            actual = hex.toString();
        } catch (java.security.NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
        if (!MessageDigest.isEqual(expected.getBytes(StandardCharsets.US_ASCII), actual.getBytes(StandardCharsets.US_ASCII))) {
            response.sendError(401); return;
        }
        chain.doFilter(new HttpServletRequestWrapper(request) {
            @Override public ServletInputStream getInputStream() {
                ByteArrayInputStream input = new ByteArrayInputStream(body);
                return new ServletInputStream() {
                    @Override public int read() { return input.read(); }
                    @Override public boolean isFinished() { return input.available() == 0; }
                    @Override public boolean isReady() { return true; }
                    @Override public void setReadListener(ReadListener listener) {
                        throw new UnsupportedOperationException("Synchronous request bodies only");
                    }
                };
            }
            @Override public BufferedReader getReader() {
                return new BufferedReader(new InputStreamReader(getInputStream(), StandardCharsets.UTF_8));
            }
        }, response);
    }
}

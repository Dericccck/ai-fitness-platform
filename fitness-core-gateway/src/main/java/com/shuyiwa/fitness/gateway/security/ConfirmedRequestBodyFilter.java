package com.shuyiwa.fitness.gateway.security;

import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import javax.servlet.*;
import javax.servlet.http.*;
import java.io.*;
import java.security.MessageDigest;

/** 缓存实际 HTTP 字节，MVC 消费请求体前计算摘要。包括无 @RequestBody 的写接口。 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 20)
public class ConfirmedRequestBodyFilter extends OncePerRequestFilter {
    public static final String BODY_HASH = ConfirmedRequestBodyFilter.class.getName() + ".sha256";
    private static final int MAX_BYTES = 2 * 1024 * 1024;
    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        if (!"POST".equals(request.getMethod()) || !request.getRequestURI().startsWith("/internal/agent-tools/")) {
            chain.doFilter(request, response); return;
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        ServletInputStream original = request.getInputStream();
        while ((count = original.read(buffer)) != -1) {
            if (out.size() + count > MAX_BYTES) { response.sendError(413); return; }
            out.write(buffer, 0, count);
        }
        byte[] body = out.toByteArray();
        request.setAttribute(BODY_HASH, sha256(body));
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
                return new BufferedReader(new InputStreamReader(getInputStream(), java.nio.charset.StandardCharsets.UTF_8));
            }
        }, response);
    }
    public static String sha256(byte[] body) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(body);
            StringBuilder result = new StringBuilder(64);
            for (byte value : digest) result.append(String.format("%02x", value & 0xff));
            return result.toString();
        } catch (java.security.NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
    }
}

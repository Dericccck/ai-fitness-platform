package com.shuyiwa.fitness.backend;

import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.apache.commons.io.IOUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.util.StringUtils;
import org.springframework.web.context.request.ServletWebRequest;
import org.springframework.web.context.request.WebRequest;

import javax.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.nio.charset.Charset;
import java.util.*;
import java.util.function.BiConsumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

public class Utils {
    private static final Log logger = LogFactory.getLog(Utils.class);

    public static void withName(String tag, Runnable runnable, BiConsumer<String, Exception>... onException) {
        long start = System.currentTimeMillis();
        String name = Thread.currentThread().getName();
        try {
            Thread.currentThread().setName(name + ":" + tag);
            runnable.run();
        } catch (Exception e) {
            if (onException.length > 0) {
                onException[0].accept(tag, e);
            } else {
                throw e;
            }
        } finally {
            logger.info("withName:task:" + tag + ":cost:" + (System.currentTimeMillis() - start));
            Thread.currentThread().setName(name);
        }
    }

    public static String injectSpace(String title) {
        return title == null ? "" :
                enOrNum(title.replaceAll("[^a-zA-Z0-9]", " $0 "))
                        .replaceAll("[\\s]+", " ");
    }

    private static String enOrNum(String text) {
        StringBuffer sb = new StringBuffer();
        Matcher matcher = Pattern.compile("[a-zA-Z0-9]{2,}").matcher(text);
        while (matcher.find()) {
            String group = matcher.group();
            matcher.appendReplacement(sb, group + " " +
                    group.replaceAll("([a-zA-Z])([0-9])", "$1 $2")
                            .replaceAll("([0-9])([a-zA-Z])", "$1 $2") + " " +
                    group.replaceAll("[a-zA-Z0-9]", " $0 "));
        }
        matcher.appendTail(sb);
        return sb.toString();
    }

    public static void main(String[] args) {
    }

    public static String requestInfo(HttpServletRequest request) {
        if (request == null) {
            return "";
        }
        String url = request.getScheme() + "://" +
                request.getServerName() + ":" + request.getServerPort() + request.getRequestURI();
        if (request.getQueryString() != null) {
            url = url + "?" + request.getQueryString();
        }
        String content = null;
        try {
            content = IOUtils.toString(request.getInputStream(), Charset.forName("utf-8"));
        } catch (IOException e1) {
            logger.warn("failed to get input stream", e1);
        }
        String parameters = request.getParameterMap().entrySet().stream()
                .map(e -> e.getKey() + ":" + Arrays.stream(e.getValue()).collect(Collectors.joining(",")))
                .collect(Collectors.joining(","));
        String ipInfo = request.getRemoteAddr() + ":" + request.getRemotePort();
        List<String> headerInfo = new ArrayList<>();
        Enumeration<String> headerNames = request.getHeaderNames();
        while (headerNames.hasMoreElements()) {
            String name = headerNames.nextElement();
            Enumeration<String> headers = request.getHeaders(name);
            while (headers.hasMoreElements()) {
                headerInfo.add(name + "=" + headers.nextElement());
            }
        }
        return url + ":c:" + content + ":p:" + parameters + ",ip:" + ipInfo + ",headers:" + headerInfo.stream().collect(Collectors.joining(","));
    }


    public static String info(WebRequest request) {
        StringBuilder sb = new StringBuilder();
        String userInfo = Optional.ofNullable(SecurityContextHolder.getContext())
                .map(SecurityContext::getAuthentication)
                .map(Authentication::getPrincipal)
                .filter(p -> p instanceof FrogUserDetails)
                .map(p -> (FrogUserDetails) p)
                .map(f -> f.getLoginUserId() + "/" + f.getPhone())
                .orElse("anonymous/00000000000");
        sb.append("user:" + userInfo + ",request:");
        List<String> headerInfo = new ArrayList<>();
        try {
            if (request instanceof ServletWebRequest) {
                HttpServletRequest httpServletRequest = ((ServletWebRequest) request).getRequest();
                if (httpServletRequest != null) {
                    sb.append(httpServletRequest.getMethod());
                    sb.append(":");
                    sb.append(httpServletRequest.getServletPath());
                    String queryString = httpServletRequest.getQueryString();
                    if (!StringUtils.isEmpty(queryString)) {
                        sb.append("?");
                        sb.append(queryString);
                    }
                    Enumeration<String> headerNames = httpServletRequest.getHeaderNames();
                    while (headerNames.hasMoreElements()) {
                        String name = headerNames.nextElement();
                        Enumeration<String> headers = httpServletRequest.getHeaders(name);
                        while (headers.hasMoreElements()) {
                            headerInfo.add(name + "=" + headers.nextElement());
                        }
                    }
                }
            }
            sb.append(" ,ua:" + request.getHeader("User-Agent"));
            sb.append(" ,refer:" + request.getHeader("referer"));
            sb.append(" ,headers:" + headerInfo.stream().collect(Collectors.joining(",")));
            return sb.toString();
        } catch (Throwable e) {

        }
        return "";
    }

    public static int compare(String v1, String v2) {
        int v = 3;
        List<String> sp1 = split(v1, v);
        List<String> sp2 = split(v2, v);
        int compare = 0;
        for (int i = 0; i < v; i++) {
            compare = Integer.compare(Integer.parseInt(sp1.get(i)), Integer.parseInt(sp2.get(i)));
            if (compare != 0) {
                break;
            }
        }
        return compare;
    }

    public static boolean before(String v1, String v2) {
        return compare(v1, v2) == -1;
    }

    private static List<String> split(String version, int v) {
        List<String> split = Arrays.stream((StringUtils.isEmpty(version) ? "0.0.0" : version).split("\\.")).collect(Collectors.toList());
        while (split.size() < v) {
            split.add(0, "0");
        }
        return split;
    }

}

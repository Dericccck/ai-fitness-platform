package com.shuyiwa.fitness.gateway.security;

import org.springframework.http.client.*;
import org.springframework.http.HttpRequest;
import java.io.IOException;

/** 从最终序列化字节计算内部摘要，不能从 DTO.toString 或入站摘要猜测出站内容。 */
public final class ConfirmedBodyForwardingInterceptor implements ClientHttpRequestInterceptor {
    @Override public ClientHttpResponse intercept(HttpRequest request, byte[] body,
            ClientHttpRequestExecution execution) throws IOException {
        if (request.getHeaders().getFirst("X-Confirmation-Id") != null) {
            request.getHeaders().set("X-Confirmed-Body-SHA256", ConfirmedRequestBodyFilter.sha256(body));
        }
        return execution.execute(request, body);
    }
}

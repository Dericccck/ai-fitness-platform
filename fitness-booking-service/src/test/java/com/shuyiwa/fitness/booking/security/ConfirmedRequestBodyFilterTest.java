package com.shuyiwa.fitness.booking.security;

import org.junit.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import javax.servlet.FilterChain;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.Assert.*;

public class ConfirmedRequestBodyFilterTest {
    private static String sha(byte[] body) throws Exception {
        StringBuilder hex = new StringBuilder();
        for (byte v : MessageDigest.getInstance("SHA-256").digest(body)) hex.append(String.format("%02x", v & 255));
        return hex.toString();
    }
    @Test public void validatesActualUtf8BytesAndPreservesBodyForMvc() throws Exception {
        byte[] body = "{\"note\":\"中文🏋️\",\"value\":null}".getBytes(StandardCharsets.UTF_8);
        MockHttpServletRequest request = request(body, sha(body));
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicBoolean called = new AtomicBoolean(false);
        new ConfirmedRequestBodyFilter().doFilter(request, response, (req, res) -> {
            assertEquals(new String(body, StandardCharsets.UTF_8), org.springframework.util.StreamUtils.copyToString(req.getInputStream(), StandardCharsets.UTF_8));
            called.set(true);
        });
        assertTrue(called.get());
    }
    @Test public void rejectsMissingHashAndTamperedBodyBeforeBusinessHandler() throws Exception {
        for (String hash : new String[]{null, sha("{}".getBytes(StandardCharsets.UTF_8))}) {
            MockHttpServletResponse response = new MockHttpServletResponse();
            AtomicBoolean called = new AtomicBoolean(false);
            new ConfirmedRequestBodyFilter().doFilter(request("{\"changed\":true}".getBytes(StandardCharsets.UTF_8), hash),
                    response, (req, res) -> called.set(true));
            assertFalse(called.get()); assertEquals(401, response.getStatus());
        }
    }
    @Test public void doesNotChangeReadOrNonConfirmationRoutes() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/internal/read");
        AtomicBoolean called = new AtomicBoolean(false);
        new ConfirmedRequestBodyFilter().doFilter(request, new MockHttpServletResponse(), (req, res) -> called.set(true));
        assertTrue(called.get());
    }
    private static MockHttpServletRequest request(byte[] body, String hash) {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/internal/write");
        request.setContent(body); request.addHeader("X-Confirmation-Id", "confirmation-id");
        if (hash != null) request.addHeader("X-Confirmed-Body-SHA256", hash);
        return request;
    }
}

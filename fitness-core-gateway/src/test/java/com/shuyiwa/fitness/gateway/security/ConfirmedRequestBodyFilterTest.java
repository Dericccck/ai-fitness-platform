package com.shuyiwa.fitness.gateway.security;

import org.junit.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.Assert.*;

public class ConfirmedRequestBodyFilterTest {
    @Test public void hashesOriginalUnicodeNullArrayOrderWithoutDtoReserialization() throws Exception {
        byte[] bytes = "{\"note\":\"中文🏋️\",\"amount\":\"1.20\",\"items\":[2,1],\"empty\":null}".getBytes(StandardCharsets.UTF_8);
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/internal/agent-tools/v1/write");
        request.setContent(bytes);
        AtomicBoolean called = new AtomicBoolean(false);
        new ConfirmedRequestBodyFilter().doFilter(request, new MockHttpServletResponse(), (req, res) -> {
            assertEquals(ConfirmedRequestBodyFilter.sha256(bytes), req.getAttribute(ConfirmedRequestBodyFilter.BODY_HASH));
            assertEquals(new String(bytes, StandardCharsets.UTF_8), org.springframework.util.StreamUtils.copyToString(req.getInputStream(), StandardCharsets.UTF_8));
            called.set(true);
        });
        assertTrue(called.get());
    }
    @Test public void rejectsOversizedBodyBeforeMvc() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/internal/agent-tools/v1/write");
        request.setContent(new byte[2 * 1024 * 1024 + 1]);
        MockHttpServletResponse response = new MockHttpServletResponse();
        new ConfirmedRequestBodyFilter().doFilter(request, response, (req, res) -> fail("must not execute"));
        assertEquals(413, response.getStatus());
    }
}

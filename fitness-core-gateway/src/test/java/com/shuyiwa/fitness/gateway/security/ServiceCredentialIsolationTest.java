package com.shuyiwa.fitness.gateway.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.gateway.api.GatewayExceptionHandler;
import com.shuyiwa.fitness.gateway.api.ToolOperationController;
import com.shuyiwa.fitness.gateway.api.ToolOperationView;
import com.shuyiwa.fitness.gateway.config.GatewayProperties;
import com.shuyiwa.fitness.gateway.config.ToolOperationClient;
import org.junit.Before;
import org.junit.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.*;
import java.time.Instant;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import static org.junit.Assert.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

public class ServiceCredentialIsolationTest {
    private static final String HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private GatewayProperties properties;
    private InternalServiceTokenVerifier verifier;
    private ToolOperationClient client;
    private MockMvc mvc;
    @Before public void setUp() {
        properties = new GatewayProperties();
        properties.setContextSigningSecret("context-test-key-no-real-secrets");
        add("adapter", "adapter-token", InternalServiceTokenVerifier.EXECUTE);
        add("agent-http", "http-token", InternalServiceTokenVerifier.EXECUTE);
        add("agent-query", "query-token", InternalServiceTokenVerifier.QUERY);
        add("worker", "worker-token", InternalServiceTokenVerifier.RECONCILE);
        verifier = new InternalServiceTokenVerifier(properties);
        client = mock(ToolOperationClient.class);
        mvc = MockMvcBuilders.standaloneSetup(new ToolOperationController(client, verifier), new WriteProbe())
                .addMappedInterceptors(new String[]{"/internal/agent-tools/**"}, new AgentContextInterceptor(
                        verifier, new AgentContextVerifier(new ObjectMapper(), properties)))
                .setCustomArgumentResolvers(new AgentContextArgumentResolver())
                .setControllerAdvice(new GatewayExceptionHandler()).build();
    }
    @Test public void workerAndQueryCredentialsCannotExecuteAndExecutionCannotReconcile() throws Exception {
        for (String token : Arrays.asList("worker-token", "query-token")) {
            mvc.perform(post("/internal/agent-tools/v1/appointments")
                    .header("X-Internal-Service-Token", token).header("X-Agent-Context", context()))
                    .andExpect(status().isForbidden());
        }
        for (String token : Arrays.asList("adapter-token", "http-token", "query-token")) {
            mvc.perform(post("/internal/agent-reconciliation/v1/operations/op-1")
                    .header("X-Internal-Service-Token", token).contentType("application/json")
                    .content(scope())).andExpect(status().isForbidden());
        }
        verifyZeroInteractions(client);
    }
    @Test public void userAndWorkerQueriesHaveIndependentTrustedActors() throws Exception {
        when(client.query("op-1", "fitness.booking.create.v1", "confirmation-1", "org-1", "actor-1", HASH))
                .thenReturn(new ToolOperationView("op-1", "fitness.booking.create.v1", "confirmation-1",
                        "org-1", "actor-1", HASH, "UNKNOWN", null));
        mvc.perform(get("/internal/agent-tools/v1/operations/op-1")
                .header("X-Internal-Service-Token", "query-token").header("X-Agent-Context", context())
                .param("toolId", "fitness.booking.create.v1").param("confirmationId", "confirmation-1")
                .param("organizationId", "org-1").param("actorId", "forged-actor").param("payloadHash", HASH))
                .andExpect(status().isOk()).andExpect(jsonPath("$.actorId").value("actor-1"));
        mvc.perform(post("/internal/agent-reconciliation/v1/operations/op-1")
                .header("X-Internal-Service-Token", "worker-token").contentType("application/json").content(scope()))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("UNKNOWN"));
        verify(client, times(2)).query("op-1", "fitness.booking.create.v1", "confirmation-1", "org-1", "actor-1", HASH);
    }
    @Test public void validExecutionCredentialStillNeedsSignedUserIdentity() throws Exception {
        mvc.perform(post("/internal/agent-tools/v1/appointments").header("X-Internal-Service-Token", "adapter-token"))
                .andExpect(status().isUnauthorized());
        for (String token : Arrays.asList("adapter-token", "http-token")) {
            mvc.perform(post("/internal/agent-tools/v1/appointments")
                    .header("X-Internal-Service-Token", token).header("X-Agent-Context", context()))
                    .andExpect(status().isOk());
        }
    }
    @Test public void legacyCredentialRequiresExplicitUnexpiredTransition() {
        properties.setInternalServiceToken("old-token");
        rejectAuthentication("old-token");
        properties.setAllowLegacyInternalServiceToken(true);
        rejectAuthentication("old-token");
        properties.setLegacyInternalServiceTokenExpiresAt(Instant.now().getEpochSecond() + 60);
        assertEquals("legacy-transition", verifier.verify("old-token", InternalServiceTokenVerifier.QUERY));
        properties.setLegacyInternalServiceTokenExpiresAt(Instant.now().getEpochSecond() - 1);
        rejectAuthentication("old-token");
    }
    @Test public void expiredAndAmbiguousCredentialFailClosed() {
        properties.getServiceCredentials().get("adapter").setExpiresAtEpochSeconds(1);
        rejectAuthentication("adapter-token");
        add("duplicate", "query-token", InternalServiceTokenVerifier.EXECUTE);
        rejectAuthentication("query-token");
    }
    @Test public void scopeCheckIsMethodAndRouteSpecific() {
        String base = "/internal/agent-tools/v1/";
        for (String route : Arrays.asList("appointments", "training/plans/drafts", "training/plans/p/publish",
                "training/plans/p/submit-review", "training/plans/p/review", "training/plans/p/days/d/execution",
                "appointments/a/cancel", "appointments/a/reschedule", "customer-service/tickets")) {
            assertEquals("adapter", verifier.verifyToolRequest("adapter-token", "POST", base + route));
            try { verifier.verifyToolRequest("query-token", "POST", base + route); fail("query scope wrote"); }
            catch (GatewayForbiddenException expected) { }
        }
        for (String route : Arrays.asList("training-context-access", "booking/operations/op", "operations/op")) {
            try { verifier.verifyToolRequest("adapter-token", "GET", base + route); fail("execution reached control API"); }
            catch (GatewayForbiddenException expected) { }
        }
        try { verifier.verifyToolRequest("adapter-token", "POST", base + "future-unreviewed-tool"); fail("unknown route"); }
        catch (GatewayForbiddenException expected) { }
    }
    private void add(String name, String token, String scope) {
        GatewayProperties.ServiceCredential credential = new GatewayProperties.ServiceCredential();
        credential.setTokenSha256(InternalServiceTokenVerifier.sha256(token));
        credential.setExpiresAtEpochSeconds(Instant.now().getEpochSecond() + 3600);
        credential.setScopes(Collections.singleton(scope)); properties.getServiceCredentials().put(name, credential);
    }
    private void rejectAuthentication(String token) {
        try { verifier.verify(token, InternalServiceTokenVerifier.QUERY); fail("credential accepted"); }
        catch (GatewaySecurityException expected) { }
    }
    private String scope() { return "{\"toolId\":\"fitness.booking.create.v1\",\"confirmationId\":\"confirmation-1\","
            + "\"organizationId\":\"org-1\",\"actorId\":\"actor-1\",\"payloadHash\":\"" + HASH + "\"}"; }
    private String context() throws Exception {
        Map<String,Object> claims = new HashMap<>();
        claims.put("sub", "actor-1"); claims.put("orgs", Collections.singletonList("org-1"));
        claims.put("roles", Collections.singletonList("STUDENT")); claims.put("nonce", "test-nonce");
        claims.put("iat", Instant.now().getEpochSecond()-1); claims.put("exp", Instant.now().getEpochSecond()+60);
        byte[] bytes = new ObjectMapper().writeValueAsBytes(claims);
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(properties.getContextSigningSecret().getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes) + "."
                + Base64.getUrlEncoder().withoutPadding().encodeToString(mac.doFinal(bytes));
    }
    @RestController public static class WriteProbe {
        @PostMapping("/internal/agent-tools/v1/appointments") public Map<String,Boolean> write(AgentContext context) {
            return Collections.singletonMap("reached", true);
        }
    }
}

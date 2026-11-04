package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.ConfirmationTokenVerifier;
import org.junit.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestTemplate;

import java.time.Instant;
import java.util.Collections;

import static org.junit.Assert.*;
import static org.mockito.Mockito.mock;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.*;
import static org.springframework.test.web.client.response.MockRestResponseCreators.*;

public class PersonalPlanClientTest {
    @Test public void forwardsSignedIdentityAndCursorToTrainingService() {
        RestTemplate http = new RestTemplate();
        MockRestServiceServer server = MockRestServiceServer.createServer(http);
        TrainingServiceProperties properties = new TrainingServiceProperties();
        properties.setBaseUrl("http://training");
        properties.setInternalServiceToken("training-test-token");
        TrainingServiceClient client = new TrainingServiceClient(http, properties, mock(ConfirmationTokenVerifier.class));
        AgentContext context = new AgentContext("student", Collections.singleton("org"),
                Collections.singleton("STUDENT"), Instant.now(), Instant.now().plusSeconds(60), "nonce");
        server.expect(requestTo("http://training/internal/training/v1/plans/mine?before=p2"))
                .andExpect(method(HttpMethod.GET)).andExpect(header("X-Actor-User-Id", "student"))
                .andExpect(header("X-Actor-Organization-Ids", "org"))
                .andExpect(header("X-Actor-Roles", "STUDENT"))
                .andRespond(withSuccess("[{\"id\":\"p1\",\"title\":\"plan\"}]", MediaType.APPLICATION_JSON));
        assertEquals("p1", client.listPersonalPlans(context, "req", "p2").get(0).id);
        server.verify();
        try { client.listPersonalPlans(context, "req", "p2&forged=true"); fail(); }
        catch (IllegalArgumentException expected) { }
    }
}

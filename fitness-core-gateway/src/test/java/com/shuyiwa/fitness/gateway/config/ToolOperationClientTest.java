package com.shuyiwa.fitness.gateway.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.gateway.api.ToolOperationView;
import org.junit.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestTemplate;
import static org.junit.Assert.*;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.*;
import static org.springframework.test.web.client.response.MockRestResponseCreators.*;

public class ToolOperationClientTest {
    private static final String HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    @Test public void allNineToolsUseOnlyTheirFixedDomainReadEndpoint() throws Exception {
        String[] names = {"fitness.booking.create.v1", "fitness.booking.reschedule.v1", "fitness.booking.cancel.v1",
                "fitness.training.plan.create_draft.v1", "fitness.training.plan.submit_review.v1",
                "fitness.training.plan.review.v1", "fitness.training.plan.publish.v1",
                "fitness.training.day.record_execution.v1", "fitness.support.ticket.create.v1"};
        for (String tool : names) {
            Fixture fixture = new Fixture();
            String domain = tool.contains("booking.") ? "booking" : tool.contains("training.") ? "training" : "customer-service";
            MockRestServiceServer server = domain.equals("booking") ? fixture.bookingServer
                    : domain.equals("training") ? fixture.trainingServer : fixture.customerServer;
            ToolOperationView view = new ToolOperationView("op-1", tool, "confirmation-1", "org-1", "actor-1", HASH, "SUCCEEDED", "result-1");
            server.expect(requestTo("http://"+domain+"/internal/"+domain+"/v1/tool-operations/op-1?toolId="+tool
                    +"&confirmationId=confirmation-1&organizationId=org-1&payloadHash="+HASH))
                    .andExpect(method(HttpMethod.GET)).andExpect(header("X-Actor-User-Id", "actor-1"))
                    .andExpect(header("X-Actor-Organization-Ids", "org-1"))
                    .andExpect(header("X-Actor-Roles", "OPERATION_RECONCILER"))
                    .andExpect(request -> assertNull(request.getHeaders().getFirst("X-Confirmation-JTI")))
                    .andRespond(withSuccess(new ObjectMapper().writeValueAsString(view), MediaType.APPLICATION_JSON));
            assertEquals("SUCCEEDED", fixture.client.query("op-1",tool,"confirmation-1","org-1","actor-1",HASH).getStatus());
            server.verify();
        }
    }
    @Test public void forgedScopeAndUnprovenFailureAreNotAcceptedAsResults() throws Exception {
        for (String variant : new String[]{"actor", "hash", "status", "reference"}) {
            Fixture fixture = new Fixture(); String tool = "fitness.booking.create.v1";
            ToolOperationView view = new ToolOperationView("op-1",tool,"confirmation-1","org-1","actor-1",HASH,"SUCCEEDED","result-1");
            if (variant.equals("actor")) view.setActorId("other");
            if (variant.equals("hash")) view.setPayloadHash(HASH.replace('a','b'));
            if (variant.equals("status")) view.setStatus("FAILED");
            if (variant.equals("reference")) view.setResultReference(null);
            fixture.bookingServer.expect(method(HttpMethod.GET)).andRespond(withSuccess(
                    new ObjectMapper().writeValueAsString(view),MediaType.APPLICATION_JSON));
            try { fixture.client.query("op-1",tool,"confirmation-1","org-1","actor-1",HASH); fail("invalid result accepted"); }
            catch (IllegalStateException expected) { }
            fixture.bookingServer.verify();
        }
    }
    @Test public void unknownToolsAndPathInjectionNeverReachDownstream() {
        Fixture fixture = new Fixture();
        try { fixture.client.query("op-1","arbitrary.tool","confirmation-1","org-1","actor-1",HASH); fail("unknown tool"); }
        catch (IllegalArgumentException expected) { }
        for (String invalid : new String[]{null, "", ".", "..", "../admin", "op/admin", "%2e%2e", "op%2fadmin",
                "op\\admin", "with space", new String(new char[129]).replace('\0', 'a')}) {
            for (int field = 0; field < 4; field++) {
                String[] scope = {"op-1", "confirmation-1", "org-1", "actor-1"};
                scope[field] = invalid;
                try { fixture.client.query(scope[0],"fitness.booking.create.v1",scope[1],scope[2],scope[3],HASH); fail("unsafe ID"); }
                catch (IllegalArgumentException expected) { }
            }
        }
        fixture.bookingServer.verify(); fixture.trainingServer.verify(); fixture.customerServer.verify();
    }
    @Test public void leadingPunctuationInStableIdsIsForwardedWithoutChangingIdentity() throws Exception {
        for (String prefix : new String[]{"_-", ":", ".", "-"}) {
            Fixture fixture = new Fixture();
            String operation = prefix + "operation", confirmation = prefix + "confirmation";
            String org = prefix + "org", actor = prefix + "actor", tool = "fitness.booking.create.v1";
            ToolOperationView view = new ToolOperationView(operation, tool, confirmation, org, actor, HASH, "SUCCEEDED", "result-1");
            fixture.bookingServer.expect(requestTo("http://booking/internal/booking/v1/tool-operations/" + operation
                    + "?toolId=" + tool + "&confirmationId=" + confirmation + "&organizationId=" + org + "&payloadHash=" + HASH))
                    .andExpect(method(HttpMethod.GET))
                    .andExpect(header("X-Request-ID", operation))
                    .andExpect(header("X-Actor-User-Id", actor))
                    .andExpect(header("X-Actor-Organization-Ids", org))
                    .andRespond(withSuccess(new ObjectMapper().writeValueAsString(view), MediaType.APPLICATION_JSON));
            ToolOperationView result = fixture.client.query(operation, tool, confirmation, org, actor, HASH);
            assertEquals("SUCCEEDED", result.getStatus());
            assertEquals(operation, result.getOperationId());
            assertEquals(confirmation, result.getConfirmationId());
            assertEquals(org, result.getOrganizationId());
            assertEquals(actor, result.getActorId());
            fixture.bookingServer.verify();
        }
    }
    private static final class Fixture {
        final RestTemplate booking = new RestTemplate(), training = new RestTemplate(), customer = new RestTemplate();
        final MockRestServiceServer bookingServer = MockRestServiceServer.bindTo(booking).build();
        final MockRestServiceServer trainingServer = MockRestServiceServer.bindTo(training).build();
        final MockRestServiceServer customerServer = MockRestServiceServer.bindTo(customer).build();
        final ToolOperationClient client;
        Fixture() {
            BookingServiceProperties b = new BookingServiceProperties(); b.setBaseUrl("http://booking"); b.setInternalServiceToken("booking-token");
            TrainingServiceProperties t = new TrainingServiceProperties(); t.setBaseUrl("http://training"); t.setInternalServiceToken("training-token");
            CustomerServiceProperties c = new CustomerServiceProperties(); c.setBaseUrl("http://customer-service"); c.setInternalServiceToken("customer-token");
            client = new ToolOperationClient(booking,training,customer,b,t,c);
        }
    }
}

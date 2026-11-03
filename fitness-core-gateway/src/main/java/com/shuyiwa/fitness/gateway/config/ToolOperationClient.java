package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.api.ToolOperationView;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.*;
import org.springframework.stereotype.Component;
import org.springframework.web.client.*;
import org.springframework.web.util.UriComponentsBuilder;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/** A fixed, read-only result-query client. No caller may supply a URL, service, role, or HTTP method. */
@Component
public class ToolOperationClient {
    private static final Set<String> BOOKING = new HashSet<>(Arrays.asList(
            "fitness.booking.create.v1", "fitness.booking.reschedule.v1", "fitness.booking.cancel.v1"));
    private static final Set<String> TRAINING = new HashSet<>(Arrays.asList(
            "fitness.training.plan.create_draft.v1", "fitness.training.plan.submit_review.v1",
            "fitness.training.plan.review.v1", "fitness.training.plan.publish.v1",
            "fitness.training.day.record_execution.v1"));
    private final RestTemplate booking, training, customer;
    private final BookingServiceProperties bookingProperties;
    private final TrainingServiceProperties trainingProperties;
    private final CustomerServiceProperties customerProperties;
    public ToolOperationClient(@Qualifier("bookingServiceRestTemplate") RestTemplate booking,
            @Qualifier("trainingServiceRestTemplate") RestTemplate training,
            @Qualifier("customerServiceRestTemplate") RestTemplate customer,
            BookingServiceProperties bookingProperties, TrainingServiceProperties trainingProperties,
            CustomerServiceProperties customerProperties) {
        this.booking = booking; this.training = training; this.customer = customer;
        this.bookingProperties = bookingProperties; this.trainingProperties = trainingProperties;
        this.customerProperties = customerProperties;
    }

    public ToolOperationView query(String operationId, String toolId, String confirmationId,
            String organizationId, String actorId, String payloadHash) {
        requireId(operationId); requireId(confirmationId); requireId(organizationId); requireId(actorId);
        if (payloadHash == null || !payloadHash.matches("[0-9a-fA-F]{64}")) {
            throw new IllegalArgumentException("Invalid operation payload fingerprint");
        }
        RestTemplate client; String baseUrl, token, domain;
        if (BOOKING.contains(toolId)) {
            client = booking; baseUrl = bookingProperties.getBaseUrl();
            token = bookingProperties.getInternalServiceToken(); domain = "booking";
        } else if (TRAINING.contains(toolId)) {
            client = training; baseUrl = trainingProperties.getBaseUrl();
            token = trainingProperties.getInternalServiceToken(); domain = "training";
        } else if ("fitness.support.ticket.create.v1".equals(toolId)) {
            client = customer; baseUrl = customerProperties.getBaseUrl();
            token = customerProperties.getInternalServiceToken(); domain = "customer-service";
        } else throw new IllegalArgumentException("Unsupported operation tool");
        if (token == null || token.trim().isEmpty()) throw new IllegalStateException("Downstream credential missing");
        String url = UriComponentsBuilder.fromHttpUrl(baseUrl.replaceAll("/$", ""))
                .pathSegment("internal", domain, "v1", "tool-operations", operationId)
                .queryParam("toolId", toolId).queryParam("confirmationId", confirmationId)
                .queryParam("organizationId", organizationId).queryParam("payloadHash", payloadHash)
                .toUriString();
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Internal-Service-Token", token);
        // This actor is derived from verified user identity or the restricted reconciliation request.
        headers.set("X-Actor-User-Id", actorId);
        headers.set("X-Actor-Roles", "OPERATION_RECONCILER");
        headers.set("X-Actor-Organization-Ids", organizationId);
        headers.set("X-Request-ID", operationId);
        try {
            ToolOperationView result = client.exchange(url, HttpMethod.GET, new HttpEntity<>(headers),
                    ToolOperationView.class).getBody();
            if (result == null || !operationId.equals(result.getOperationId()) || !toolId.equals(result.getToolId())
                    || !confirmationId.equals(result.getConfirmationId()) || !organizationId.equals(result.getOrganizationId())
                    || !actorId.equals(result.getActorId()) || !payloadHash.equalsIgnoreCase(result.getPayloadHash())) {
                throw new IllegalStateException("Invalid downstream operation binding");
            }
            if (!"UNKNOWN".equals(result.getStatus()) && !"SUCCEEDED".equals(result.getStatus())) {
                throw new IllegalStateException("Unproven downstream operation status");
            }
            if ("SUCCEEDED".equals(result.getStatus())
                    && (result.getResultReference() == null || result.getResultReference().trim().isEmpty())) {
                throw new IllegalStateException("Operation success lacks durable result reference");
            }
            return result;
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 403) throw new GatewayForbiddenException("操作结果不在授权范围内");
            // A 404 or malformed downstream response is not proof the original operation did not run.
            throw new IllegalStateException("操作结果查询失败", exception);
        } catch (RestClientException exception) {
            throw new IllegalStateException("操作结果暂时无法核实", exception);
        }
    }
    private static void requireId(String value) {
        if (value == null || !value.matches("[A-Za-z0-9._:-]{1,128}")
                || ".".equals(value) || "..".equals(value)) {
            throw new IllegalArgumentException("Invalid operation identity");
        }
    }
}

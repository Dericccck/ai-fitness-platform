package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import com.shuyiwa.fitness.gateway.api.TrainingToolInputs;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.ConfirmationTokenVerifier;
import com.shuyiwa.fitness.gateway.security.GatewayConflictException;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import com.shuyiwa.fitness.gateway.security.GatewayResourceNotFoundException;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

/**
 * Gateway 到训练服务的固定客户端。
 *
 * <p>Agent 永远不能直接连接训练服务。这里把签名上下文中的主体、角色和机构范围映射为
 * 内部 Header，训练服务还会用自己的 Token 和数据库关系再校验一次，形成双层防线。</p>
 */
@Component
public class TrainingServiceClient {

    private final RestTemplate restTemplate;
    private final TrainingServiceProperties properties;
    private final ConfirmationTokenVerifier confirmationTokenVerifier;

    public TrainingServiceClient(RestTemplate trainingServiceRestTemplate,
                                 TrainingServiceProperties properties,
                                 ConfirmationTokenVerifier confirmationTokenVerifier) {
        this.restTemplate = trainingServiceRestTemplate;
        this.properties = properties;
        this.confirmationTokenVerifier = confirmationTokenVerifier;
    }

    public ToolViews.TrainingPlanView get(AgentContext context, String planId, String requestId) {
        return exchange(context, requestId, null, HttpMethod.GET,
                "/internal/training/v1/plans/" + planId, null).toToolView();
    }

    public ToolViews.TrainingPlanView createDraft(AgentContext context, String requestId,
                                                  String confirmationToken,
                                                  TrainingToolInputs.DraftInput input) {
        confirmationTokenVerifier.verify(confirmationToken, context, "CREATE_TRAINING_DRAFT",
                input.getOrganizationId() + ":" + input.getStudentId(), requestId);
        requireOrganization(context, input.getOrganizationId());
        return exchange(context, requestId, confirmationToken, HttpMethod.POST,
                "/internal/training/v1/plans/drafts", input).toToolView();
    }

    public ToolViews.TrainingPlanView submitReview(AgentContext context, String planId,
                                                   String requestId, String confirmationToken) {
        confirmationTokenVerifier.verify(confirmationToken, context, "SUBMIT_TRAINING_REVIEW", planId, requestId);
        return exchange(context, requestId, confirmationToken, HttpMethod.POST,
                "/internal/training/v1/plans/" + planId + "/submit-review", null).toToolView();
    }

    public ToolViews.TrainingPlanView review(AgentContext context, String planId, String requestId,
                                             String confirmationToken, TrainingToolInputs.ReviewInput input) {
        confirmationTokenVerifier.verify(confirmationToken, context, "REVIEW_TRAINING_PLAN", planId, requestId);
        return exchange(context, requestId, confirmationToken, HttpMethod.POST,
                "/internal/training/v1/plans/" + planId + "/review", input).toToolView();
    }

    public ToolViews.TrainingPlanView publish(AgentContext context, String planId,
                                              String requestId, String confirmationToken) {
        confirmationTokenVerifier.verify(confirmationToken, context, "PUBLISH_TRAINING_PLAN", planId, requestId);
        return exchange(context, requestId, confirmationToken, HttpMethod.POST,
                "/internal/training/v1/plans/" + planId + "/publish", null).toToolView();
    }

    private TrainingServiceViews.Plan exchange(AgentContext context, String requestId,
                                                String confirmationToken, HttpMethod method,
                                                String path, Object body) {
        if (properties.getInternalServiceToken().trim().isEmpty()) {
            throw new IllegalStateException("training service internal token is not configured");
        }
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Internal-Service-Token", properties.getInternalServiceToken());
        headers.set("X-Actor-User-Id", context.getSubjectUserId());
        headers.set("X-Actor-Roles", String.join(",", context.getRoles()));
        headers.set("X-Actor-Organization-Ids", String.join(",", context.getOrganizationIds()));
        headers.set("X-Request-ID", requestId);
        if (confirmationToken != null && !confirmationToken.trim().isEmpty()) {
            headers.set("X-Confirmation-Token", confirmationToken);
        }
        try {
            ResponseEntity<TrainingServiceViews.Plan> response = restTemplate.exchange(
                    properties.getBaseUrl().replaceAll("/$", "") + path,
                    method, new HttpEntity<>(body, headers), TrainingServiceViews.Plan.class);
            TrainingServiceViews.Plan bodyResponse = response.getBody();
            if (bodyResponse == null) {
                throw new IllegalStateException("training service returned an empty response");
            }
            return bodyResponse;
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 403) {
                throw new GatewayForbiddenException("training resource is outside the authorized scope");
            }
            if (exception.getStatusCode().value() == 404) {
                throw new GatewayResourceNotFoundException("training plan was not found");
            }
            if (exception.getStatusCode().value() == 409) {
                throw new GatewayConflictException("training plan was changed by another request");
            }
            throw new IllegalArgumentException("training service rejected the request");
        } catch (RestClientException exception) {
            throw new IllegalStateException("training service is temporarily unavailable", exception);
        }
    }

    private void requireOrganization(AgentContext context, String organizationId) {
        if (organizationId == null || !context.canAccessOrganization(organizationId)) {
            throw new GatewayForbiddenException("organization is outside the authorized scope");
        }
    }
}

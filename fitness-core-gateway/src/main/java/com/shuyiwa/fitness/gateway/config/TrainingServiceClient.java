package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import com.shuyiwa.fitness.gateway.api.TrainingToolInputs;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.ConfirmationTokenClaims;
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

import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;

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
        ConfirmationTokenClaims claims = confirmationTokenVerifier.verify(
                confirmationToken, context, "fitness.training.plan.create_draft.v1",
                "CREATE_TRAINING_DRAFT", input.getOrganizationId() + ":" + input.getStudentId(), requestId);
        if (!input.getOrganizationId().equals(claims.getOrganizationId())) {
            throw new GatewayForbiddenException("confirmation organization does not match request");
        }
        requireOrganization(context, input.getOrganizationId());
        return exchange(context, requestId, claims, HttpMethod.POST,
                "/internal/training/v1/plans/drafts", input).toToolView();
    }

    public ToolViews.TrainingPlanView submitReview(AgentContext context, String planId,
                                                   String requestId, String confirmationToken) {
        ConfirmationTokenClaims claims = confirmationTokenVerifier.verify(
                confirmationToken, context, "fitness.training.plan.submit_review.v1",
                "SUBMIT_TRAINING_REVIEW", planId, requestId);
        return exchange(context, requestId, claims, HttpMethod.POST,
                "/internal/training/v1/plans/" + planId + "/submit-review", null).toToolView();
    }

    public ToolViews.TrainingPlanView review(AgentContext context, String planId, String requestId,
                                             String confirmationToken, TrainingToolInputs.ReviewInput input) {
        ConfirmationTokenClaims claims = confirmationTokenVerifier.verify(
                confirmationToken, context, "fitness.training.plan.review.v1",
                "REVIEW_TRAINING_PLAN", planId, requestId);
        return exchange(context, requestId, claims, HttpMethod.POST,
                "/internal/training/v1/plans/" + planId + "/review", input).toToolView();
    }

    public ToolViews.TrainingPlanView publish(AgentContext context, String planId,
                                              String requestId, String confirmationToken) {
        ConfirmationTokenClaims claims = confirmationTokenVerifier.verify(
                confirmationToken, context, "fitness.training.plan.publish.v1",
                "PUBLISH_TRAINING_PLAN", planId, requestId);
        return exchange(context, requestId, claims, HttpMethod.POST,
                "/internal/training/v1/plans/" + planId + "/publish", null).toToolView();
    }

    public List<ToolViews.TrainingDayExecutionView> listExecutions(AgentContext context, String planId,
                                                                    String requestId) {
        TrainingServiceViews.Execution[] executions = exchangeExecutions(context, requestId, null,
                HttpMethod.GET, "/internal/training/v1/plans/" + planId + "/executions", null);
        return Arrays.stream(executions).map(TrainingServiceViews.Execution::toToolView)
                .collect(Collectors.toList());
    }

    public ToolViews.TrainingDayExecutionView recordExecution(AgentContext context, String planId,
                                                               String dayId, String requestId,
                                                               String confirmationToken,
                                                               TrainingToolInputs.ExecutionInput input) {
        if (input == null || input.getDayId() == null || !dayId.equals(input.getDayId())) {
            throw new IllegalArgumentException("execution day does not match request path");
        }
        ConfirmationTokenClaims claims = confirmationTokenVerifier.verify(
                confirmationToken, context, "fitness.training.day.record_execution.v1",
                "RECORD_TRAINING_DAY_EXECUTION", planId + ":" + dayId, requestId);
        return exchangeExecution(context, requestId, claims, HttpMethod.POST,
                "/internal/training/v1/plans/" + planId + "/days/" + dayId + "/execution", input)
                .toToolView();
    }

    private TrainingServiceViews.Plan exchange(AgentContext context, String requestId,
                                                ConfirmationTokenClaims confirmationClaims, HttpMethod method,
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
        if (confirmationClaims != null) {
            // 不把原始 Token 继续传给训练服务，只传 Gateway 已验签的声明；JTI 的真正消费
            // 由训练服务和业务写入放在同一事务中完成。
            headers.set("X-Confirmation-Id", confirmationClaims.getConfirmationId());
            headers.set("X-Confirmation-JTI", confirmationClaims.getJti());
            headers.set("X-Confirmation-Tool-ID", confirmationClaims.getToolId());
            headers.set("X-Confirmation-Action", confirmationClaims.getAction());
            headers.set("X-Confirmation-Organization-ID", confirmationClaims.getOrganizationId());
            headers.set("X-Confirmation-Resource", confirmationClaims.getResource());
            headers.set("X-Confirmation-Payload-Hash", confirmationClaims.getPayloadHash());
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

    private TrainingServiceViews.Execution exchangeExecution(AgentContext context, String requestId,
                                                              ConfirmationTokenClaims confirmationClaims,
                                                              HttpMethod method, String path, Object body) {
        return exchangeTyped(context, requestId, confirmationClaims, method, path, body,
                TrainingServiceViews.Execution.class);
    }

    private TrainingServiceViews.Execution[] exchangeExecutions(AgentContext context, String requestId,
                                                                 ConfirmationTokenClaims confirmationClaims,
                                                                 HttpMethod method, String path, Object body) {
        return exchangeTyped(context, requestId, confirmationClaims, method, path, body,
                TrainingServiceViews.Execution[].class);
    }

    private <T> T exchangeTyped(AgentContext context, String requestId,
                                ConfirmationTokenClaims confirmationClaims, HttpMethod method,
                                String path, Object body, Class<T> responseType) {
        if (properties.getInternalServiceToken().trim().isEmpty()) {
            throw new IllegalStateException("training service internal token is not configured");
        }
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Internal-Service-Token", properties.getInternalServiceToken());
        headers.set("X-Actor-User-Id", context.getSubjectUserId());
        headers.set("X-Actor-Roles", String.join(",", context.getRoles()));
        headers.set("X-Actor-Organization-Ids", String.join(",", context.getOrganizationIds()));
        headers.set("X-Request-ID", requestId);
        if (confirmationClaims != null) {
            headers.set("X-Confirmation-Id", confirmationClaims.getConfirmationId());
            headers.set("X-Confirmation-JTI", confirmationClaims.getJti());
            headers.set("X-Confirmation-Tool-ID", confirmationClaims.getToolId());
            headers.set("X-Confirmation-Action", confirmationClaims.getAction());
            headers.set("X-Confirmation-Organization-ID", confirmationClaims.getOrganizationId());
            headers.set("X-Confirmation-Resource", confirmationClaims.getResource());
            headers.set("X-Confirmation-Payload-Hash", confirmationClaims.getPayloadHash());
        }
        try {
            ResponseEntity<T> response = restTemplate.exchange(
                    properties.getBaseUrl().replaceAll("/$", "") + path,
                    method, new HttpEntity<>(body, headers), responseType);
            if (response.getBody() == null) {
                throw new IllegalStateException("training service returned an empty response");
            }
            return response.getBody();
        } catch (HttpClientErrorException exception) {
            if (exception.getStatusCode().value() == 403) {
                throw new GatewayForbiddenException("training resource is outside the authorized scope");
            }
            if (exception.getStatusCode().value() == 404) {
                throw new GatewayResourceNotFoundException("training resource was not found");
            }
            if (exception.getStatusCode().value() == 409) {
                throw new GatewayConflictException("training resource was changed by another request");
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

package com.shuyiwa.fitness.gateway.api;

import com.shuyiwa.fitness.gateway.config.TrainingServiceClient;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 训练领域 Tool Gateway 接口。
 *
 * <p>写操作必须携带确认凭证和请求 ID；Controller 不直接访问数据库，只把已认证的
 * AgentContext 交给固定 Client，避免模型输入绕过权限和审计。</p>
 */
@RestController
@RequestMapping("/internal/agent-tools/v1/training/plans")
public class TrainingToolController {

    private final TrainingServiceClient client;

    public TrainingToolController(TrainingServiceClient client) {
        this.client = client;
    }

    @GetMapping("/{planId}")
    public ToolViews.TrainingPlanView get(AgentContext context, @PathVariable String planId,
                                         @RequestHeader("X-Request-ID") String requestId) {
        return client.get(context, planId, requestId);
    }

    @PostMapping("/drafts")
    public ToolViews.TrainingPlanView createDraft(AgentContext context,
                                                  @RequestHeader("X-Request-ID") String requestId,
                                                  @RequestHeader("X-Confirmation-Token") String confirmationToken,
                                                  @RequestBody TrainingToolInputs.DraftInput input) {
        return client.createDraft(context, requestId, confirmationToken, input);
    }

    @PostMapping("/{planId}/submit-review")
    public ToolViews.TrainingPlanView submitReview(AgentContext context, @PathVariable String planId,
                                                   @RequestHeader("X-Request-ID") String requestId,
                                                   @RequestHeader("X-Confirmation-Token") String confirmationToken) {
        return client.submitReview(context, planId, requestId, confirmationToken);
    }

    @PostMapping("/{planId}/review")
    public ToolViews.TrainingPlanView review(AgentContext context, @PathVariable String planId,
                                             @RequestHeader("X-Request-ID") String requestId,
                                             @RequestHeader("X-Confirmation-Token") String confirmationToken,
                                             @RequestBody TrainingToolInputs.ReviewInput input) {
        return client.review(context, planId, requestId, confirmationToken, input);
    }

    @PostMapping("/{planId}/publish")
    public ToolViews.TrainingPlanView publish(AgentContext context, @PathVariable String planId,
                                              @RequestHeader("X-Request-ID") String requestId,
                                              @RequestHeader("X-Confirmation-Token") String confirmationToken) {
        return client.publish(context, planId, requestId, confirmationToken);
    }
}

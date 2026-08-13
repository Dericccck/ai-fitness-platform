package com.shuyiwa.fitness.training.api;

import com.shuyiwa.fitness.training.security.TrainingActor;
import com.shuyiwa.fitness.training.service.TrainingPlanService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** 结构化训练计划内部 API；所有写操作都必须经过训练服务状态机。 */
@RestController
@RequestMapping("/internal/training/v1/plans")
public class TrainingPlanController {

    private final TrainingPlanService service;

    public TrainingPlanController(TrainingPlanService service) {
        this.service = service;
    }

    @PostMapping("/drafts")
    @ResponseStatus(HttpStatus.CREATED)
    public TrainingPlanView createDraft(TrainingActor actor, @RequestBody TrainingPlanRequest request) {
        return service.createAgentDraft(actor, request);
    }

    @PostMapping("/{planId}/submit-review")
    public TrainingPlanView submitReview(TrainingActor actor, @PathVariable String planId) {
        return service.submitForReview(actor, planId);
    }

    @PostMapping("/{planId}/review")
    public TrainingPlanView review(TrainingActor actor, @PathVariable String planId,
                                  @RequestBody TrainingReviewRequest request) {
        return service.review(actor, planId, request);
    }

    @PostMapping("/{planId}/publish")
    public TrainingPlanView publish(TrainingActor actor, @PathVariable String planId) {
        return service.publish(actor, planId);
    }

    @GetMapping("/{planId}")
    public TrainingPlanView get(TrainingActor actor, @PathVariable String planId) {
        return service.get(actor, planId);
    }
}

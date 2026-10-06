package com.shuyiwa.fitness.training.api;

import com.shuyiwa.fitness.training.outbox.TrainingOutboxRepository;
import com.shuyiwa.fitness.training.security.TrainingActor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import java.util.List;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/internal/training/v1/outbox")
public class TrainingOutboxAdminController {
    private final TrainingOutboxRepository repository;
    public TrainingOutboxAdminController(TrainingOutboxRepository repository) { this.repository = repository; }

    @GetMapping("/dead")
    public List<TrainingDeadOutboxView> listDead(TrainingActor actor) {
        requireAdmin(actor);
        return repository.listDead().stream().filter(e -> actor.canAccessOrganization(e.organizationId))
                .map(TrainingOutboxAdminController::view).collect(Collectors.toList());
    }

    @PostMapping("/dead/{id}/replay")
    public TrainingDeadOutboxView replay(TrainingActor actor, @PathVariable long id,
            @RequestBody TrainingOutboxReplayRequest request) {
        requireAdmin(actor);
        TrainingOutboxRepository.DeadOutboxEvent event = repository.findDead(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "DEAD 事件不存在"));
        if (!actor.canAccessOrganization(event.organizationId))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "事件不在当前主体授权范围内");
        String reason = request == null ? null : request.getReason();
        if (reason == null || reason.trim().isEmpty() || reason.trim().length() > 500)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "必须提供 1-500 字符的重放原因");
        if (!repository.replayDead(id, actor.getUserId(), reason.trim()))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "事件已被其他操作处理");
        return view(repository.findById(id).orElseThrow(() ->
                new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "重放后事件不存在")));
    }

    private static void requireAdmin(TrainingActor actor) {
        if (!actor.isAdministrator()) throw new ResponseStatusException(HttpStatus.FORBIDDEN, "仅管理员可处置 DEAD 事件");
    }
    private static TrainingDeadOutboxView view(TrainingOutboxRepository.DeadOutboxEvent e) {
        return new TrainingDeadOutboxView(e.id,e.eventKey,e.eventType,e.aggregateId,e.organizationId,
                e.status,e.attemptCount,e.lastError,e.createdAt,e.replayCount,e.lastReplayedBy,e.lastReplayedAt);
    }
}

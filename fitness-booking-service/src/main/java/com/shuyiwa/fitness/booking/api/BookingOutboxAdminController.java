package com.shuyiwa.fitness.booking.api;

import com.shuyiwa.fitness.booking.outbox.BookingOutboxRepository;
import com.shuyiwa.fitness.booking.security.BookingActor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.stream.Collectors;

/** Outbox DEAD 查询与重放入口，仅机构管理员或系统管理员可用。 */
@RestController
@RequestMapping("/internal/booking/v1/outbox")
public class BookingOutboxAdminController {
    private final BookingOutboxRepository repository;

    public BookingOutboxAdminController(BookingOutboxRepository repository) {
        this.repository = repository;
    }

    @GetMapping("/dead")
    public List<BookingDeadOutboxView> listDead(BookingActor actor) {
        requireAdmin(actor);
        return repository.listDead().stream()
                .filter(event -> actor.canAccessOrganization(event.organizationId))
                .map(BookingOutboxAdminController::view)
                .collect(Collectors.toList());
    }

    @PostMapping("/dead/{id}/replay")
    public BookingDeadOutboxView replay(BookingActor actor, @PathVariable long id,
                                        @RequestBody BookingOutboxReplayRequest request) {
        requireAdmin(actor);
        BookingOutboxRepository.DeadOutboxEvent event = repository.findDead(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "DEAD 事件不存在"));
        if (!actor.canAccessOrganization(event.organizationId)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "事件不在当前主体授权范围内");
        }
        String reason = request == null ? null : request.getReason();
        if (reason == null || reason.trim().isEmpty() || reason.trim().length() > 500) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "必须提供 1-500 字符的重放原因");
        }
        if (!repository.replayDead(id, actor.getUserId(), reason.trim())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "事件已被其他操作处理");
        }
        return view(repository.findById(id).orElse(event));
    }

    private static void requireAdmin(BookingActor actor) {
        if (!actor.isAdministrator()) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "仅管理员可处置 DEAD 事件");
        }
    }

    private static BookingDeadOutboxView view(BookingOutboxRepository.DeadOutboxEvent event) {
        return new BookingDeadOutboxView(event.id, event.eventKey, event.eventType, event.aggregateId,
                event.organizationId, event.status, event.attemptCount, event.lastError,
                event.createdAt, event.replayCount, event.lastReplayedBy, event.lastReplayedAt);
    }
}

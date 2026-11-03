"""Only confirmed Java business writes have automatic durable-result reconciliation."""

RECONCILABLE_TOOL_IDS = frozenset(
    {
        "fitness.booking.create.v1",
        "fitness.booking.reschedule.v1",
        "fitness.booking.cancel.v1",
        "fitness.training.plan.create_draft.v1",
        "fitness.training.plan.submit_review.v1",
        "fitness.training.plan.review.v1",
        "fitness.training.plan.publish.v1",
        "fitness.training.day.record_execution.v1",
        "fitness.support.ticket.create.v1",
    }
)

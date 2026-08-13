package com.shuyiwa.fitness.training.service;

import com.shuyiwa.fitness.training.api.TrainingApiException;
import com.shuyiwa.fitness.training.api.TrainingPlanRequest;
import com.shuyiwa.fitness.training.api.TrainingPlanView;
import com.shuyiwa.fitness.training.api.TrainingReviewRequest;
import com.shuyiwa.fitness.training.domain.TrainingDay;
import com.shuyiwa.fitness.training.domain.TrainingItem;
import com.shuyiwa.fitness.training.domain.TrainingPlan;
import com.shuyiwa.fitness.training.domain.TrainingPlanStatus;
import com.shuyiwa.fitness.training.repository.TrainingPlanRepository;
import com.shuyiwa.fitness.training.security.TrainingActor;
import com.shuyiwa.fitness.training.security.TrainingConfirmation;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 训练计划业务边界。
 *
 * <p>这里是 Agent 和训练数据库之间的唯一写入口。Agent 生成的内容先进入 DRAFT，任何
 * 未经教练或组织管理员审核的内容都不能进入 PUBLISHED；学员查询到的正式计划只能来自
 * PUBLISHED 状态。每个状态变化都要求请求 ID，并在同一个数据库事务中写入审计。</p>
 */
@Service
public class TrainingPlanService {

    private final TrainingPlanRepository repository;

    public TrainingPlanService(TrainingPlanRepository repository) {
        this.repository = repository;
    }

    @Transactional
    public TrainingPlanView createAgentDraft(TrainingActor actor, TrainingPlanRequest request) {
        requireRequired(request.getOrganizationId(), "organizationId");
        requireRequired(request.getStudentId(), "studentId");
        requireRequired(request.getCoachId(), "coachId");
        requireRequired(request.getTitle(), "title");
        requireRequired(request.getGoalType(), "goalType");
        requireStudentScope(actor, request.getOrganizationId(), request.getStudentId());
        if (!repository.isOrganizationMember(request.getOrganizationId(), request.getStudentId())) {
            throw forbidden("学员不是该机构的有效成员");
        }
        if (!repository.isCoachForStudent(request.getOrganizationId(), request.getCoachId(), request.getStudentId())) {
            throw forbidden("指定教练不是该学员在该机构的有效教练");
        }
        validatePlanContent(request.getTitle(), request.getGoalType(), request.getDays());

        TrainingPlan plan = new TrainingPlan();
        plan.setId(TrainingPlanRepository.newId());
        plan.setOrganizationId(request.getOrganizationId());
        plan.setStudentId(request.getStudentId());
        plan.setCoachId(request.getCoachId());
        plan.setTitle(request.getTitle().trim());
        plan.setGoalType(request.getGoalType().trim());
        plan.setSource("AGENT");
        plan.setStatus(TrainingPlanStatus.DRAFT);
        plan.setVersion(0);
        plan.setCreatedBy(actor.getUserId());
        plan.setDays(withGeneratedIds(request.getDays()));
        TrainingConfirmation confirmation = requireConfirmation(actor,
                "fitness.training.plan.create_draft.v1", "CREATE_TRAINING_DRAFT",
                request.getOrganizationId(), request.getOrganizationId() + ":" + request.getStudentId());
        TrainingPlan persisted = repository.insertDraft(plan, actor.getRequestId(), confirmation);
        return view(repository.findById(persisted.getId()).orElseThrow(() -> notFound("训练计划不存在")));
    }

    @Transactional
    public TrainingPlanView submitForReview(TrainingActor actor, String planId) {
        TrainingPlan plan = loadVisiblePlan(actor, planId, false);
        requireCanOperatePlan(actor, plan);
        transition(plan, TrainingPlanStatus.PENDING_REVIEW, actor, "SUBMIT_REVIEW", null,
                "fitness.training.plan.submit_review.v1", "SUBMIT_TRAINING_REVIEW");
        return reload(plan.getId());
    }

    @Transactional
    public TrainingPlanView review(TrainingActor actor, String planId, TrainingReviewRequest request) {
        TrainingPlan plan = loadVisiblePlan(actor, planId, false);
        if (!actor.isAdministrator() && (!actor.hasRole(TrainingActor.COACH)
                || !actor.getUserId().equals(plan.getCoachId()))) {
            throw forbidden("只有负责教练或机构管理员可以审核训练计划");
        }
        if (request == null || request.getDecision() == null) {
            throw invalid("审核决定不能为空");
        }
        TrainingPlanStatus target;
        if ("APPROVE".equalsIgnoreCase(request.getDecision())) {
            target = TrainingPlanStatus.APPROVED;
        } else if ("REJECT".equalsIgnoreCase(request.getDecision())) {
            target = TrainingPlanStatus.REJECTED;
        } else {
            throw invalid("审核决定只能是 APPROVE 或 REJECT");
        }
        if (target == TrainingPlanStatus.REJECTED && isBlank(request.getComment())) {
            throw invalid("驳回时必须填写原因");
        }
        transition(plan, target, actor, "REVIEW", request.getComment(),
                "fitness.training.plan.review.v1", "REVIEW_TRAINING_PLAN");
        return reload(plan.getId());
    }

    @Transactional
    public TrainingPlanView publish(TrainingActor actor, String planId) {
        TrainingPlan plan = loadVisiblePlan(actor, planId, false);
        if (!actor.isAdministrator() && (!actor.hasRole(TrainingActor.COACH)
                || !actor.getUserId().equals(plan.getCoachId()))) {
            throw forbidden("只有负责教练或机构管理员可以发布训练计划");
        }
        transition(plan, TrainingPlanStatus.PUBLISHED, actor, "PUBLISH", null,
                "fitness.training.plan.publish.v1", "PUBLISH_TRAINING_PLAN");
        return reload(plan.getId());
    }

    public TrainingPlanView get(TrainingActor actor, String planId) {
        return view(loadVisiblePlan(actor, planId, true));
    }

    private void transition(TrainingPlan plan, TrainingPlanStatus target, TrainingActor actor,
                             String action, String comment, String toolId, String confirmationAction) {
        if (repository.wasRequestApplied(plan.getId(), actor.getRequestId())) {
            // 客户端超时后重试时，第一次可能已经提交成功；幂等重试直接返回当前事实。
            return;
        }
        if (!plan.getStatus().canTransitionTo(target)) {
            throw invalid("当前状态不能执行该操作: " + plan.getStatus());
        }
        TrainingConfirmation confirmation = requireConfirmation(
                actor, toolId, confirmationAction, plan.getOrganizationId(), plan.getId()
        );
        if (!repository.transition(plan, target, action, actor.getUserId(), actor.getRequestId(), comment,
                confirmation)) {
            throw new TrainingApiException(HttpStatus.CONFLICT, "训练计划已被其他请求修改，请重新读取后操作");
        }
    }

    private TrainingConfirmation requireConfirmation(TrainingActor actor, String toolId,
                                                      String action, String expectedOrganizationId,
                                                      String resource) {
        TrainingConfirmation confirmation = actor.getConfirmation();
        if (confirmation == null) {
            throw new TrainingApiException(HttpStatus.UNAUTHORIZED, "缺少确认凭证");
        }
        if (!toolId.equals(confirmation.getToolId())
                || !action.equals(confirmation.getAction())
                || !expectedOrganizationId.equals(confirmation.getOrganizationId())
                || !resource.equals(confirmation.getResource())
                || !actor.canAccessOrganization(confirmation.getOrganizationId())
                || !confirmation.getPayloadHash().matches("[0-9a-fA-F]{64}")) {
            throw new TrainingApiException(HttpStatus.FORBIDDEN, "确认凭证范围与训练操作不匹配");
        }
        return confirmation;
    }

    private TrainingPlan loadVisiblePlan(TrainingActor actor, String planId, boolean allowStudentPublishedOnly) {
        TrainingPlan plan = repository.findById(planId).orElseThrow(() -> notFound("训练计划不存在"));
        if (!actor.canAccessOrganization(plan.getOrganizationId())) {
            throw forbidden("训练计划所属机构不在当前业务主体授权范围内");
        }
        if (!actor.isAdministrator() && actor.hasRole(TrainingActor.COACH)
                && !actor.getUserId().equals(plan.getCoachId())) {
            throw forbidden("教练不能访问非本人负责的训练计划");
        }
        if (actor.hasRole(TrainingActor.STUDENT) && !actor.getUserId().equals(plan.getStudentId())) {
            throw forbidden("学员只能访问本人的训练计划");
        }
        if (!actor.isAdministrator() && !actor.hasRole(TrainingActor.COACH)
                && !actor.hasRole(TrainingActor.STUDENT)) {
            throw forbidden("当前主体没有训练计划权限");
        }
        if (allowStudentPublishedOnly && actor.hasRole(TrainingActor.STUDENT)
                && plan.getStatus() != TrainingPlanStatus.PUBLISHED) {
            throw forbidden("学员只能查看已发布训练计划");
        }
        return plan;
    }

    private void requireCanOperatePlan(TrainingActor actor, TrainingPlan plan) {
        if (actor.isAdministrator()) {
            return;
        }
        if (actor.hasRole(TrainingActor.COACH) && actor.getUserId().equals(plan.getCoachId())) {
            return;
        }
        throw forbidden("只有负责教练或机构管理员可以提交审核");
    }

    private void requireStudentScope(TrainingActor actor, String organizationId, String studentId) {
        if (!actor.canAccessOrganization(organizationId)) {
            throw forbidden("机构不在当前业务主体授权范围内");
        }
        if (actor.isAdministrator()) {
            if (!actor.hasRole(TrainingActor.SYSTEM_ADMIN) && !actor.hasRole(TrainingActor.ORGANIZATION_ADMIN)) {
                throw forbidden("当前主体没有机构管理权限");
            }
            return;
        }
        if (actor.hasRole(TrainingActor.STUDENT) && !actor.getUserId().equals(studentId)) {
            throw forbidden("学员只能为本人生成训练计划草案");
        }
        if (actor.hasRole(TrainingActor.COACH)
                && !repository.isCoachForStudent(organizationId, actor.getUserId(), studentId)) {
            throw forbidden("教练不是该学员的负责教练");
        }
        if (!actor.hasRole(TrainingActor.STUDENT) && !actor.hasRole(TrainingActor.COACH)) {
            throw forbidden("当前主体没有训练计划权限");
        }
    }

    private void validatePlanContent(String title, String goalType, List<TrainingDay> days) {
        if (title.trim().length() > 128 || goalType.trim().length() > 32) {
            throw invalid("计划标题或目标类型长度超限");
        }
        if (days == null || days.isEmpty() || days.size() > 31) {
            throw invalid("训练日数量必须在 1 到 31 之间");
        }
        Set<Integer> dayNumbers = new HashSet<>();
        for (TrainingDay day : days) {
            if (day == null || day.getDayNumber() == null || day.getDayNumber() < 1
                    || !dayNumbers.add(day.getDayNumber()) || isBlank(day.getTitle())) {
                throw invalid("训练日必须有唯一的正整数序号和标题");
            }
            if (day.getItems() == null || day.getItems().isEmpty() || day.getItems().size() > 100) {
                throw invalid("每个训练日必须包含 1 到 100 个动作");
            }
            Set<Integer> orders = new HashSet<>();
            for (TrainingItem item : day.getItems()) {
                if (item == null || isBlank(item.getExerciseName()) || item.getSortOrder() == null
                        || item.getSortOrder() < 1 || !orders.add(item.getSortOrder())
                        || item.getSets() == null || item.getSets() < 1 || item.getSets() > 100
                        || isBlank(item.getReps())) {
                    throw invalid("动作必须有唯一顺序、动作名称、组数和次数目标");
                }
                if (item.getTargetRpe() != null && (item.getTargetRpe().signum() < 0
                        || item.getTargetRpe().doubleValue() > 10)) {
                    throw invalid("目标 RPE 必须在 0 到 10 之间");
                }
            }
        }
    }

    private List<TrainingDay> withGeneratedIds(List<TrainingDay> input) {
        for (TrainingDay day : input) {
            day.setId(TrainingPlanRepository.newId());
            for (TrainingItem item : day.getItems()) {
                item.setId(TrainingPlanRepository.newId());
            }
        }
        return input;
    }

    private TrainingPlanView reload(String id) {
        return view(repository.findById(id).orElseThrow(() -> notFound("训练计划不存在")));
    }

    private TrainingPlanView view(TrainingPlan plan) {
        TrainingPlanView view = new TrainingPlanView();
        view.setId(plan.getId());
        view.setOrganizationId(plan.getOrganizationId());
        view.setStudentId(plan.getStudentId());
        view.setCoachId(plan.getCoachId());
        view.setTitle(plan.getTitle());
        view.setGoalType(plan.getGoalType());
        view.setSource(plan.getSource());
        view.setStatus(plan.getStatus());
        view.setVersion(plan.getVersion());
        view.setCreatedBy(plan.getCreatedBy());
        view.setReviewedBy(plan.getReviewedBy());
        view.setPublishedBy(plan.getPublishedBy());
        view.setReviewComment(plan.getReviewComment());
        view.setCreatedAt(plan.getCreatedAt());
        view.setUpdatedAt(plan.getUpdatedAt());
        view.setReviewedAt(plan.getReviewedAt());
        view.setPublishedAt(plan.getPublishedAt());
        view.setDays(plan.getDays());
        return view;
    }

    private static void requireRequired(String value, String field) {
        if (isBlank(value)) {
            throw invalid(field + " 不能为空");
        }
    }

    private static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    private static TrainingApiException invalid(String message) {
        return new TrainingApiException(HttpStatus.BAD_REQUEST, message);
    }

    private static TrainingApiException forbidden(String message) {
        return new TrainingApiException(HttpStatus.FORBIDDEN, message);
    }

    private static TrainingApiException notFound(String message) {
        return new TrainingApiException(HttpStatus.NOT_FOUND, message);
    }
}

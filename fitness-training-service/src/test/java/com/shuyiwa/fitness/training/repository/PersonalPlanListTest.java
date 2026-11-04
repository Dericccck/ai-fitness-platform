package com.shuyiwa.fitness.training.repository;

import com.shuyiwa.fitness.training.api.TrainingApiException;
import com.shuyiwa.fitness.training.api.TrainingPlanSummary;
import com.shuyiwa.fitness.training.security.TrainingActor;
import com.shuyiwa.fitness.training.service.TrainingPlanService;
import com.shuyiwa.fitness.training.testsupport.IsolatedJdbcTest;
import org.junit.Before;
import org.junit.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;

import static org.junit.Assert.*;

public class PersonalPlanListTest extends IsolatedJdbcTest {
    private JdbcTemplate jdbc;
    private TrainingPlanService service;

    @Before public void setup() {
        jdbc = new JdbcTemplate(newDataSource());
        jdbc.execute("CREATE TABLE training_plan (id VARCHAR(128), organization_id VARCHAR(128), student_id VARCHAR(128), coach_id VARCHAR(128), title VARCHAR(128), status VARCHAR(24))");
        service = new TrainingPlanService(new TrainingPlanRepository(jdbc));
        for (int i = 0; i < 55; i++) {
            jdbc.update("INSERT INTO training_plan VALUES (?, ?, ?, ?, ?, ?)", String.format("p%03d", i), "org", "student", "coach", "plan", "PUBLISHED");
        }
        jdbc.update("INSERT INTO training_plan VALUES ('z1','org','other','coach','other','PUBLISHED')");
        jdbc.update("INSERT INTO training_plan VALUES ('z2','other','student','coach','other org','PUBLISHED')");
        jdbc.update("INSERT INTO training_plan VALUES ('z3','org','student','coach','draft','DRAFT')");
    }

    private TrainingActor actor(String... roles) {
        return new TrainingActor("student", new HashSet<>(Arrays.asList(roles)), Collections.singleton("org"), "req");
    }

    @Test public void onlyPersonalPublishedPlansAndStablePagesAreVisible() {
        TrainingActor student = actor(TrainingActor.STUDENT);
        List<TrainingPlanSummary> first = service.listPersonalPublished(student, null);
        assertEquals(51, first.size());
        assertEquals("p054", first.get(0).getId());
        List<TrainingPlanSummary> second = service.listPersonalPublished(student, first.get(49).getId());
        assertEquals(5, second.size());
        assertEquals("p004", second.get(0).getId());
    }

    @Test public void mixedCoachRoleRespectsExistingCoachBoundary() {
        assertTrue(service.listPersonalPublished(actor(TrainingActor.STUDENT, TrainingActor.COACH), null).isEmpty());
        assertEquals(51, service.listPersonalPublished(actor(TrainingActor.STUDENT, TrainingActor.COACH, TrainingActor.ORGANIZATION_ADMIN), null).size());
    }

    @Test public void nonStudentAndInvalidCursorAreRejected() {
        try { service.listPersonalPublished(actor(TrainingActor.COACH), null); fail(); }
        catch (TrainingApiException expected) { assertEquals(403, expected.getStatus().value()); }
        try { service.listPersonalPublished(actor(TrainingActor.STUDENT), "x&other=y"); fail(); }
        catch (TrainingApiException expected) { assertEquals(400, expected.getStatus().value()); }
    }
}

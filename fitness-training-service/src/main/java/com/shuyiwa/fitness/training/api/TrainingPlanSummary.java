package com.shuyiwa.fitness.training.api;

/** Minimal personal plan information for a selection list. */
public class TrainingPlanSummary {
    private final String id;
    private final String title;

    public TrainingPlanSummary(String id, String title) {
        this.id = id;
        this.title = title;
    }

    public String getId() { return id; }
    public String getTitle() { return title; }
}

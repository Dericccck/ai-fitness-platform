package com.shuyiwa.fitness.training.domain;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

public class TrainingDay {
    private String id;
    private Integer dayNumber;
    private String title;
    private LocalDate scheduledDate;
    private List<TrainingItem> items = new ArrayList<>();

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public Integer getDayNumber() { return dayNumber; }
    public void setDayNumber(Integer dayNumber) { this.dayNumber = dayNumber; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public LocalDate getScheduledDate() { return scheduledDate; }
    public void setScheduledDate(LocalDate scheduledDate) { this.scheduledDate = scheduledDate; }
    public List<TrainingItem> getItems() { return items; }
    public void setItems(List<TrainingItem> items) { this.items = items == null ? new ArrayList<>() : items; }
}

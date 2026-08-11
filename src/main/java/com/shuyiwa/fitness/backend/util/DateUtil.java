package com.shuyiwa.fitness.backend.util;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.time.temporal.TemporalAdjusters;
import java.util.Date;

public class DateUtil {

    public static final String YYYYMMDDHHMMSS = "yyyy-MM-dd HH:mm:ss";
    public static final String YYYYMMDD = "yyyy-MM-dd";


    /**
     * Date类型转LocalDateTime
     * <p>
     *
     * @param date date类型时间
     * @return LocalDateTime
     */
    public static LocalDateTime toLocalDateTime(Date date) {
        return LocalDateTime.ofInstant(date.toInstant(), ZoneId.systemDefault());
    }

    /**
     * Date类型转LocalDate
     * <p>
     *
     * @param date date类型时间
     * @return LocalDate
     */
    public static LocalDate toLocalDate(Date date) {
        return toLocalDateTime(date).toLocalDate();
    }

    /**
     * Date类型转LocalTime
     * <p>
     *
     * @param date date类型时间
     * @return LocalTime
     */
    public static LocalTime toLocalTime(Date date) {
        return toLocalDateTime(date).toLocalTime();
    }

    /**
     * LocalDateTime 类型转 Date
     *
     * @param localDateTime localDateTime
     * @return 转换后的Date类型日期
     */
    public static Date toDate(LocalDateTime localDateTime) {
        return Date.from(localDateTime.atZone(ZoneId.systemDefault()).toInstant());
    }

    /**
     * LocalDate类型转Date
     *
     * @param localDate localDate
     * @return 转换后的Date类型日期
     */
    public static Date toDate(LocalDate localDate) {
        return toDate(localDate.atStartOfDay());
    }

    /**
     * LocalTime类型转Date
     *
     * @param localTime localTime
     * @return 转换后的Date类型日期
     */
    public static Date toDate(LocalTime localTime) {
        return toDate(LocalDateTime.of(localDate(), localTime));
    }
    /**
     * 获取 LocalDate
     */
    private static LocalDate localDate() {
        return localDateTime().toLocalDate();
    }

    /**
     * 获取 LocalTime
     */
    private static LocalTime localTime() {
        return localDateTime().toLocalTime();
    }

    /**
     * 获取 LocalDateTime
     */
    private static LocalDateTime localDateTime() {
        return LocalDateTime.now();
    }


    public static String formatYMD(LocalDateTime date) {
        return format2Str(date, YYYYMMDD);
    }

    public static String formatYMDHMS(LocalDateTime date) {
        return format2Str(date, YYYYMMDDHHMMSS);
    }


    /**
     *  @return 当天开始时间 00:00
     */
    public static LocalDateTime firstTimeOfDay() {
        return LocalDate.now().atStartOfDay();
    }

    /**
     *  @return 当天最后时间 23:59:59
     */
    public static LocalDateTime lastTimeOfDay() {
        return LocalDate.now().atTime(23,59,59);
    }

    /**
     *  @return 明天日期
     */
    public static LocalDate getTomorrow() {
        return LocalDate.now().plusDays(1);
    }

    /**
     *  @return 本月第一天
     */
    public static LocalDate firstDayOfThisMonth() {
        return LocalDate.now().with(TemporalAdjusters.firstDayOfMonth());
    }

    /**
     *  @return 本月最后一天
     */
    public static LocalDate lastDayOfMonth() {
        return LocalDate.now().with(TemporalAdjusters.lastDayOfMonth());
    }

    /**
     * 判断当前日期是否在两个日期期间内
     * @param before
     * @param after
     * @return true or false
     */
    public static boolean twoDatePeriod(LocalDateTime before,LocalDateTime after){
        LocalDateTime now = LocalDateTime.now();
        return now.isAfter(before) && now.isBefore(after);
    }

    public static String formatYMD2Str(LocalDateTime date) {
        return format2Str(date, YYYYMMDD);
    }

    public static String formatYMDHMS2Str(LocalDateTime date) {
        return format2Str(date, YYYYMMDDHHMMSS);
    }

    public static LocalDateTime formatYMD(String date) {
        return format(date, YYYYMMDD);
    }

    public static LocalDateTime formatYMDHMS(String date) {
        return format(date, YYYYMMDDHHMMSS);
    }

    public static String format2Str(LocalDateTime date, String pattern) {
        return date == null?null:getStringFormat(date,pattern);
    }

    public static LocalDateTime format(String date, String pattern) {
        return date == null?null:getDateFormat(date,pattern);
    }

    /**
     *  日期解析字符串
     *  @return String
     */
    public static String getStringFormat(LocalDateTime date, String pattern) {
        DateTimeFormatter format = DateTimeFormatter.ofPattern(pattern);
        return date.format(format);
    }

    /**
     *  字符串解析日期
     *  @return LocalDateTime
     */
    public static LocalDateTime getDateFormat(String date, String pattern) {
        DateTimeFormatter format = DateTimeFormatter.ofPattern(pattern);
        return LocalDateTime.parse(date,format);
    }
}

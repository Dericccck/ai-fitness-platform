package com.shuyiwa.fitness.backend.domain.dict;

import com.fasterxml.jackson.annotation.JsonValue;

import java.util.*;

public enum Authority {
    ADMIN("【权限】系统管理"),
    ADMIN_CHANNEL("【权限】渠道管理员"),
    ADMIN_ORGANIZATION("【权限】机构管理员"),
    SUPER_ADMIN_ORGANIZATION("【权限】机构高级管理员", ADMIN_ORGANIZATION),
    ADMIN_ARTICLE("文章"),
    ADMIN_ORGANIZATIONS("机构管理"),
    ADMIN_USER_TASKS("勋章管理"),
    ADMIN_FEEDS("频道"),
    ADMIN_WORKS("作品"),
    ADMIN_APPS("APP管理"),
    ADMIN_LOGIN_USER("用户管理"),
    ADMIN_CONTEST_SCHEDULES("赛程管理"),
    ADMIN_FEED_POP("赛事弹窗"),
    ADMIN_MESSAGES("通知管理"),
    JUDGE("江苏6评委通道"),
    ACTIVITY("活动"),
    NOTICE("公告"),
    FROG_RANK("榜单"),
    CONTESTANT_INFO("选手"),
    ITEM("积分商城"),
    MARKETING("奖励活动"),
    WORKS_CARD("作品铭牌"),
    HOT_WORD("热词管理"),
    CERTIFICATE("证书"),
    CONTEST_SEASON("赛事"),
    BALLOT("蛙票"),
    REDEEM("兑换"),
    CHANNEL("渠道"),
    HOME_CHANNEL("首页频道"),
    ARTICLE_DATA("文章数据"),
    HYPERLINKS_DATA("必胜客兑换"),
    CONTEST_OP("赛事运营(组)", ADMIN_FEEDS, ADMIN_CONTEST_SCHEDULES, ADMIN_WORKS, FROG_RANK, CERTIFICATE),
    NORMAL_OP("全局运营(组)", ADMIN_ARTICLE, ACTIVITY, ADMIN_USER_TASKS, ITEM, ADMIN_MESSAGES, MARKETING, WORKS_CARD, CONTEST_SEASON,REDEEM),
    COACH("教练"),
    ;

    private String authorityName;
    private List<Authority> children = new ArrayList<>();

    Authority(String authorityName) {
        this.authorityName = authorityName;
    }

    Authority(String authorityName, Authority... authorities) {
        this.authorityName = authorityName;
        Arrays.stream(authorities).forEach(children::add);
    }

    public List<Authority> getChildren() {
        return children;
    }

    public String getAuthorityName() {
        return authorityName;
    }

    @JsonValue
    public Map<String, String> getJson() {
        Map<String, String> map = new HashMap<>();
        map.put("id", name());
        map.put("authorityName", authorityName);
        return map;
    }


}

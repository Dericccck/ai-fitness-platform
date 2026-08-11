package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import javax.annotation.PostConstruct;
import java.io.IOException;
import java.util.*;
import java.util.stream.Collectors;

@Service
public class MenuService {

    private static final Log logger = LogFactory.getLog(MenuService.class);
    private final Map<Authority, List<Menu>> authorityListMap = new HashMap<>();

    private final List<Menu> groups = new ArrayList<>();
    @Autowired
    ObjectMapper objectMapper;

    @PostConstruct
    void init() {


//        newMenu(1, "首页", "/dashboard", "fa fa-dashboard", Authority.ADMIN);
////        newMenu(2, Authority.ADMIN_ORGANIZATION.getAuthorityName(), "/worksUpload", "fa fa-user-plus", Authority.ADMIN, Authority.ADMIN_ORGANIZATION);
//        newMenu(3, "江苏7选手数据", "/contestantInfoList/40288a8d78e8e99c0179176af69c2439", "fa fa-users", Authority.ADMIN, Authority.CONTESTANT_INFO);
//        newGroup(4, "赛事运营", "/contest", "fa fa-folder", true);
////        newMenu("/contest", 1, Authority.ADMIN_FEEDS.getAuthorityName(), "/feeds", "fa fa-columns", Authority.ADMIN, Authority.ADMIN_FEEDS);
//        newMenu("/contest", 1, "赛事频道", "/homeFeed", "fa fa-columns", Authority.ADMIN, Authority.ADMIN_FEEDS);
//        newMenu("/contest", 2, "社区频道", "/eventFeed", "fa fa-columns", Authority.ADMIN, Authority.ADMIN_FEEDS);
//        newMenu("/contest", 3, Authority.ADMIN_CONTEST_SCHEDULES.getAuthorityName(), "/contestSchedules", "fa fa-bars", Authority.ADMIN, Authority.ADMIN_CONTEST_SCHEDULES);
//        newMenu("/contest", 4, Authority.ADMIN_FEED_POP.getAuthorityName(), "/feedPop", "fa fa-bars", Authority.ADMIN, Authority.ADMIN_FEED_POP);
//        newMenu("/contest", 5, Authority.ADMIN_WORKS.getAuthorityName(), "/works", "fa fa-graduation-cap", Authority.ADMIN, Authority.ADMIN_WORKS);
//        newMenu("/contest", 6, Authority.FROG_RANK.getAuthorityName(), "/rankingList", "fa fa-list-ol", Authority.ADMIN, Authority.FROG_RANK);
//        newMenu("/contest", 7, Authority.CERTIFICATE.getAuthorityName(), "/certificatePage", "fa fa-certificate", Authority.ADMIN, Authority.CERTIFICATE);
//        newGroup(7, "全局运营", "/operation", "fa fa-folder", true);
//        newMenu("/operation", 1, Authority.ADMIN_ARTICLE.getAuthorityName(), "/articles", "fa fa-book", Authority.ADMIN, Authority.ADMIN_ARTICLE);
//        newMenu("/operation", 2, Authority.ACTIVITY.getAuthorityName(), "/activityList", "fa fa-trophy", Authority.ADMIN, Authority.ACTIVITY);
//        newMenu("/operation", 3, Authority.NOTICE.getAuthorityName(), "/noticeList", "fa fa-envelope", Authority.ADMIN, Authority.NOTICE);
//        newMenu("/operation", 4, Authority.ADMIN_USER_TASKS.getAuthorityName(), "/userTasks", "fa fa-trophy", Authority.ADMIN, Authority.ADMIN_USER_TASKS);
//        newMenu("/operation", 5, Authority.ITEM.getAuthorityName(), "/itemList", "fa fa-shopping-cart", Authority.ADMIN, Authority.ITEM);
//        newMenu("/operation", 6, Authority.ADMIN_MESSAGES.getAuthorityName(), "/messages", "fa fa-envelope", Authority.ADMIN, Authority.ADMIN_MESSAGES);
//        newMenu("/operation", 7, Authority.MARKETING.getAuthorityName(), "/marketingList", "fa fa-money", Authority.ADMIN, Authority.MARKETING);
//        newMenu("/operation", 8, Authority.HOME_CHANNEL.getAuthorityName(), "/operateFeed", "fa fa-columns", Authority.ADMIN, Authority.HOME_CHANNEL);
//        newMenu("/operation", 9, Authority.WORKS_CARD.getAuthorityName(), "/worksCardPage", "fa fa-tags", Authority.ADMIN, Authority.WORKS_CARD);
//        newMenu("/operation", 10, Authority.HOT_WORD.getAuthorityName(), "/hotWord", "fa fa-tags", Authority.ADMIN, Authority.HOT_WORD);
//        newMenu("/operation", 11, Authority.CONTEST_SEASON.getAuthorityName(), "/contestSeasonList", "fa fa-bars", Authority.ADMIN, Authority.CONTEST_SEASON);
//        newMenu("/operation", 12, Authority.BALLOT.getAuthorityName(), "/ballotPage", "fa fa-bars", Authority.ADMIN, Authority.BALLOT);
//        newMenu("/operation", 13, Authority.REDEEM.getAuthorityName(), "/redeemEnter", "fa fa-exchange", Authority.ADMIN, Authority.REDEEM);
//        newMenu("/operation", 14, Authority.CHANNEL.getAuthorityName(), "/channel", "fa fa-exchange", Authority.ADMIN, Authority.CHANNEL);
//        newGroup(8, "系统", "/system", "fa fa-folder", true);
//        newMenu("/system", 1, Authority.ADMIN_ORGANIZATIONS.getAuthorityName(), "/organizations", "fa fa-graduation-cap", Authority.ADMIN, Authority.ADMIN_ORGANIZATIONS);
//        newMenu("/system", 2, "权限管理", "/loginUsers", "fa fa-book", Authority.ADMIN);
//        newMenu("/system", 3, "API接口文档", "/core/doc/api", "fa fa-th-list", Authority.ADMIN);
//        newMenu("/system", 4, Authority.ADMIN_APPS.getAuthorityName(), "/appStore", "fa fa-cloud-upload", Authority.ADMIN, Authority.ADMIN_APPS);
//        newGroup(9, "数据", "/data", "fa fa-folder", true);
//        newMenu("/data", 1, Authority.ARTICLE_DATA.getAuthorityName(), "/articleData", "fa fa-book", Authority.ADMIN, Authority.ARTICLE_DATA);
//        newMenu("/data", 2, Authority.HYPERLINKS_DATA.getAuthorityName(), "/hyperlinksData", "fa fa-book", Authority.ADMIN, Authority.HYPERLINKS_DATA);
//        newGroup(10, "历史赛事", "/history", "fa fa-folder", true);
//        newMenu("/history",3, Authority.JUDGE.getAuthorityName(), "/worksScore/40288a8d71f8f8d9017211d352495452", "fa fa-thumbs-up", Authority.ADMIN, Authority.JUDGE);
//        newMenu("/history",4, "江苏6选手数据", "/contestantInfoList/40288a8d71f8f8d9017211d352495452", "fa fa-users", Authority.ADMIN, Authority.CONTESTANT_INFO);
//        newMenu("/history",5, "中国蓝选手数据", "/contestantInfoList/40288a8d73562e6101735b0931633238", "fa fa-users", Authority.ADMIN, Authority.CONTESTANT_INFO);
        newMenu(11, "首页", "/index", "fa fa-dashboard", Authority.ADMIN_ORGANIZATION);
        newGroup(12, "数据", "/data", "fa fa-tags", true);
        newMenu("/data",1, "概览", "/overview", "fa fa-align-left", Authority.ADMIN_ORGANIZATION);
        newMenu("/data",2, "店铺数据", "/shopData", "fa fa-bar-chart", Authority.ADMIN_ORGANIZATION);
        newMenu("/data",3, "教练数据", "/coachData", "fa fa-address-book", Authority.ADMIN_ORGANIZATION);
        newMenu("/data",4, "用户数据", "/userData", "fa fa-address-card", Authority.ADMIN_ORGANIZATION);

        newMenu(13, "团队", "/team", "fa fa-weixin", Authority.ADMIN_ORGANIZATION);
        newMenu(14, "用户", "/user", "fa fa-user", Authority.ADMIN_ORGANIZATION);
        newMenu(15, "课程管理", "/course", "fa fa-list", Authority.ADMIN_ORGANIZATION);
        newMenu(16, "合约管理", "/contract", "fa fa-yen", Authority.ADMIN_ORGANIZATION);
        newMenu(17,"公告","/noticeList","fa fa-envelope",Authority.ADMIN_ORGANIZATION);
        newGroup(18, "其它", "/other", "fa fa-tags", false);
        newMenu("/other", 1, "日程与休假", "/system", "fa fa-cog", Authority.ADMIN_ORGANIZATION);
        newMenu("/other", 2, "消息记录", "/message", "fa fa-envelope-open", Authority.ADMIN_ORGANIZATION);


//        newMenu(Platform.frog_org, 7, "首页", "/dashboard", "fa fa-dashboard", Authority.ADMIN_ORGANIZATION, Authority.SUPER_ADMIN_ORGANIZATION, Authority.ADMIN_CHANNEL);
//        newGroup(Platform.frog_org, 8, "机构", "/organization", "fa fa-folder", true);
////        newMenu(Platform.frog_org, 102, "渠道", "/channel", "fa fa-dashboard", Authority.ADMIN_CHANNEL);
//        newMenu(Platform.frog_org, "/organization", 1, "推荐管理", "/recommend", "fa fa-building", Authority.ADMIN_ORGANIZATION, Authority.SUPER_ADMIN_ORGANIZATION);
//        newMenu(Platform.frog_org, "/organization", 2, "作品", "/worksList", "fa fa-film", Authority.ADMIN_ORGANIZATION, Authority.SUPER_ADMIN_ORGANIZATION);
//        newMenu(Platform.frog_org, "/organization", 3, "机构详情", "/organizationDetail", "fa fa-building", Authority.ADMIN_ORGANIZATION, Authority.SUPER_ADMIN_ORGANIZATION);
//        newMenu(Platform.frog_org, "/organization", 4, "粉丝", "/followerList", "fa fa-group", Authority.ADMIN_ORGANIZATION, Authority.SUPER_ADMIN_ORGANIZATION);
////        newMenu(Platform.frog_org, 106, "报名", "/apply", "fa fa-hand-o-up", Authority.ADMIN_ORGANIZATION, Authority.SUPER_ADMIN_ORGANIZATION);
//        newMenu(Platform.frog_org, "/organization", 5, "赛事", "/contestSeasonList", "fa fa-hand-o-up", Authority.ADMIN_ORGANIZATION, Authority.SUPER_ADMIN_ORGANIZATION);
//        newMenu(Platform.frog_org, "/organization", 6, "管理", "/manage", "fa fa-wrench", Authority.ADMIN_ORGANIZATION, Authority.SUPER_ADMIN_ORGANIZATION);
//        newGroup(Platform.frog_org, 9, "渠道", "/channel", "fa fa-folder", "mt-5", true);
//        newMenu(Platform.frog_org, "/channel", 2, "渠道数据", "/channelRegister", "fa fa-yen", "nav-org", Authority.ADMIN_CHANNEL);

    }

    private void newGroup(Platform platform, int order, String name, String url, String icon, boolean open) {
        newGroup(platform, order, name, url, icon, null, open);
    }

    private void newGroup(Platform platform, int order, String name, String url, String icon, String className, boolean open) {
        Menu menu = new Menu();
        menu.setName(name);
        menu.setOrder(order);
        menu.setUrl(url);
        menu.setIcon(icon);
        menu.setOpen(open);
        menu.setClassName(className);
        menu.setPlatform(platform);
        groups.add(menu);
    }

    private void newGroup(int order, String name, String url, String icon, boolean open) {
        Menu menu = new Menu();
        menu.setName(name);
        menu.setOrder(order);
        menu.setUrl(url);
        menu.setIcon(icon);
        menu.setOpen(open);
        groups.add(menu);
    }

    private void newMenu(String parentUrl, int order, String name, String url, String icon, Authority... authorities) {
        Menu menu = new Menu();
        menu.setOrder(order);
        menu.setName(name);
        menu.setUrl(url);
        menu.setIcon(icon);
        menu.setParentUrl(parentUrl);
        for (Authority authority : authorities) {
            authorityListMap.computeIfAbsent(authority, key -> new ArrayList<>()).add(menu);
        }
    }

    private void newMenu(int order, String name, String url, String icon, Authority... authorities) {
        newMenu(Platform.fitness_console, order, name, url, icon, authorities);
    }

    private void newMenu(Platform platform, String parentUrl, int order, String name, String url, String icon, Authority... authorities) {
        newMenu(platform, parentUrl, order, name, url, icon, null, authorities);
    }

    private void newMenu(Platform platform, String parentUrl, int order, String name, String url, String icon, String className, Authority... authorities) {
        Menu menu = new Menu();
        menu.setPlatform(platform);
        menu.setName(name);
        menu.setUrl(url);
        menu.setIcon(icon);
        menu.setOrder(order);
        menu.setClassName(className);
        menu.setParentUrl(parentUrl);
        for (Authority authority : authorities) {
            authorityListMap.computeIfAbsent(authority, key -> new ArrayList<>()).add(menu);
        }

    }

    private void newMenu(Platform platform, int order, String name, String url, String icon, Authority... authorities) {
        Menu menu = new Menu();
        menu.setPlatform(platform);
        menu.setName(name);
        menu.setUrl(url);
        menu.setIcon(icon);
        menu.setOrder(order);
        for (Authority authority : authorities) {
            authorityListMap.computeIfAbsent(authority, key -> new ArrayList<>()).add(menu);
        }

    }

    public enum Platform {
        fitness_console
    }

    public List<Menu> menus(Authority authority) {
        return authorityListMap.getOrDefault(authority, new ArrayList<>());
    }

    private Menu copy(Menu menu) {
        try {
            return objectMapper.readValue(objectMapper.writeValueAsString(menu), Menu.class);
        } catch (IOException e) {
            return null;
        }
    }

    public List<Menu> addParent(List<Menu> menus) {
        List<Menu> result = new ArrayList<>();
        for (Menu group : groups) {
            group = copy(group);
            for (Iterator<Menu> iterator = menus.iterator(); iterator.hasNext(); ) {
                Menu menu = iterator.next();
                if (group.getUrl().equals(menu.getParentUrl())) {
                    group.add(menu);
                    iterator.remove();
                }
            }
            if (group.getChildren() != null && group.getChildren().size() > 0) {
                result.add(group);
            }
        }
        for (Menu menu : menus) {
            result.add(menu);
        }
        result.stream().filter(menu -> menu.getChildren() != null).forEach(menu -> menu.setChildren(menu.getChildren().stream().sorted(Comparator.comparingInt(x -> x.getOrder())).collect(Collectors.toList())));
        return result.stream().sorted(Comparator.comparingInt(x -> x.getOrder())).collect(Collectors.toList());
    }

    public static class Menu {
        private Platform platform;
        private String name;
        private String url;
        private String parentUrl;
        private String icon;
        @JsonProperty("class")
        private String className;
        private int order;
        private boolean open;
        private List<Menu> children;

        public Platform getPlatform() {
            return platform;
        }

        public void setPlatform(Platform platform) {
            this.platform = platform;
        }

        public boolean isOpen() {
            return open;
        }

        public void setOpen(boolean open) {
            this.open = open;
        }

        public List<Menu> getChildren() {
            return children;
        }

        public void setChildren(List<Menu> children) {
            this.children = children;
        }

        public String getName() {
            return name;
        }

        public void setName(String name) {
            this.name = name;
        }

        public String getUrl() {
            return url;
        }

        public void setUrl(String url) {
            this.url = url;
        }

        public String getIcon() {
            return icon;
        }

        public void setIcon(String icon) {
            this.icon = icon;
        }

        public String getParentUrl() {
            return parentUrl;
        }

        public void setParentUrl(String parentUrl) {
            this.parentUrl = parentUrl;
        }

        public int getOrder() {
            return order;
        }

        public void setOrder(int order) {
            this.order = order;
        }

        public void add(Menu menu) {
            if (children == null) {
                children = new ArrayList<>();
            }
            children.add(menu);
        }

        public String getClassName() {
            return className;
        }

        public void setClassName(String className) {
            this.className = className;
        }
    }
}

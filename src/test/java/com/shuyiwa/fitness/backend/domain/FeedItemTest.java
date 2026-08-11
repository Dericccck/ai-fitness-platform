package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

public class FeedItemTest {
    private static final String img = "https://picsum.photos/200/300";

    @Test
    public void generateInitDate() throws JsonProcessingException {
        int id = 1;
        double score = 1000;
        FeedItemBuilder build = new FeedItemBuilder(null)
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.TOP)
                .__().id(id++)
                .__().score(score--)
                .__().type(FeedItem.EntityType.BANNER)
                .__().__().newItem()
                .__().__().__().id(id++)
                .__().__().__().score(score--)
                .__().__().__().img(img)
                .__().__().__().title("banner里的一篇文章")
                .__().__().__().type(FeedItem.EntityType.ARTICLE)
                .__().__().__().entityId(1)
                .__().__().__().build()
                .__().__().newItem()
                .__().__().__().id(id++)
                .__().__().__().score(score--)
                .__().__().__().img(img)
                .__().__().__().title("banner里的一个机构")
                .__().__().__().type(FeedItem.EntityType.ORGANIZATION)
                .__().__().__().entityId(1)
                .__().__().__().build()
                .__().__().newItem()
                .__().__().__().id(id++)
                .__().__().__().score(score--)
                .__().__().__().img(img)
                .__().__().__().title("banner里的一个FEED")
                .__().__().__().type(FeedItem.EntityType.FEED)
                .__().__().__().entityId(1)
                .__().__().__().build()
                .__().__().newItem()
                .__().__().__().id(id++)
                .__().__().__().score(score--)
                .__().__().__().img(img)
                .__().__().__().title("banner里的另一个FEED")
                .__().__().__().type(FeedItem.EntityType.FEED)
                .__().__().__().entityId("eventFeed")
                .__().__().__().build()
                .__().__().newItem()
                .__().__().__().id(id++)
                .__().__().__().score(score--)
                .__().__().__().img(img)
                .__().__().__().title("banner里的一个个人中心")
                .__().__().__().type(FeedItem.EntityType.MY)
                .__().__().__().build()
                .__().__().newItem()
                .__().__().__().id(id++)
                .__().__().__().score(score--)
                .__().__().__().img(img)
                .__().__().__().title("banner里的一个作品")
                .__().__().__().type(FeedItem.EntityType.WORKS)
                .__().__().__().entityId(1)
                .__().__().__().build()
                .__().__().newItem()
                .__().__().__().id(id++)
                .__().__().__().score(score--)
                .__().__().__().img(img)
                .__().__().__().title("banner里的另一个作品")
                .__().__().__().type(FeedItem.EntityType.WORKS)
                .__().__().__().entityId(2)
                .__().__().__().build()
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.TOP)
                .__().id(id++)
                .__().score(score--)
                .__().type(FeedItem.EntityType.ORGANIZATIONS)
                .__().__().newItem()
                .__().__().__().id(id++)
                .__().__().__().score(score--)
                .__().__().__().img(img)
                .__().__().__().title("banner里的第一个机构")
                .__().__().__().type(FeedItem.EntityType.ORGANIZATION)
                .__().__().__().entityId(1)
                .__().__().__().build()
                .__().__().newItem()
                .__().__().__().id(id++)
                .__().__().__().score(score--)
                .__().__().__().img(img)
                .__().__().__().title("banner里的第二个机构")
                .__().__().__().type(FeedItem.EntityType.ORGANIZATION)
                .__().__().__().entityId(2)
                .__().__().__().build()
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .position(FeedItem.FeedPosition.TOP)
                .__().id(id++)
                .__().score(score--)
                .__().type(FeedItem.EntityType.BUTTON_GROUP)
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.BOTTOM)
                .__().id(id++)
                .__().score(score--)
                .__().img(img)
                .__().title("Feed里的一篇文章")
                .__().type(FeedItem.EntityType.ARTICLE)
                .__().entityId(1)
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.BOTTOM)
                .__().id(id++)
                .__().score(score--)
                .__().img(img)
                .__().title("Feed里的另一篇文章")
                .__().type(FeedItem.EntityType.ARTICLE)
                .__().entityId(2)
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.BOTTOM)
                .__().id(id++)
                .__().score(score--)
                .__().img(img)
                .__().title("Feed里的一个作品")
                .__().type(FeedItem.EntityType.WORKS)
                .__().entityId(1)
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.BOTTOM)
                .__().id(id++)
                .__().score(score--)
                .__().img(img)
                .__().title("Feed里的另一个作品")
                .__().type(FeedItem.EntityType.WORKS)
                .__().entityId(2)
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.BOTTOM)
                .__().id(id++)
                .__().score(score--)
                .__().img(img)
                .__().title("Feed里的第三个作品")
                .__().type(FeedItem.EntityType.WORKS)
                .__().entityId(3)
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.BOTTOM)
                .__().id(id++)
                .__().score(score--)
                .__().img(img)
                .__().title("Feed里的第四个作品")
                .__().type(FeedItem.EntityType.WORKS)
                .__().entityId(4)
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.BOTTOM)
                .__().id(id++)
                .__().score(score--)
                .__().img(img)
                .__().title("Feed里的第五个作品")
                .__().type(FeedItem.EntityType.WORKS)
                .__().entityId(5)
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.BOTTOM)
                .__().id(id++)
                .__().score(score--)
                .__().img(img)
                .__().title("Feed里的第6个作品")
                .__().type(FeedItem.EntityType.WORKS)
                .__().entityId(6)
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.BOTTOM)
                .__().id(id++)
                .__().score(score--)
                .__().img(img)
                .__().title("Feed里的第7个作品")
                .__().type(FeedItem.EntityType.WORKS)
                .__().entityId(7)
                .__().build()
                .newItem()
                .__().feed("homeFeed")
                .__().position(FeedItem.FeedPosition.BOTTOM)
                .__().id(id++)
                .__().score(score--)
                .__().img(img)
                .__().title("Feed里的第8个作品")
                .__().type(FeedItem.EntityType.WORKS)
                .__().entityId(8)
                .__().build();
        ObjectMapper mapper = new ObjectMapper();
        mapper.enable(SerializationFeature.INDENT_OUTPUT);
//        System.out.println(mapper.writeValueAsString(build.item.getChildren()));
        build.item.getChildren().stream().forEach(this::toSql);
    }

    private void toSql(FeedItem feedItem) {
        StringBuilder sb = new StringBuilder();
        sb.append("insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted) \n");
        sb.append(String.format(" values('%s','%s','%s','%s','%s','%s','%s','%s','%s','%s',false); \n", feedItem.getId(), feedItem.getScore(), feedItem.getFeedId(),
                feedItem.getEntityType(), feedItem.getEntity(),
                feedItem.getImg(), feedItem.getTitle(), feedItem.isVideo(),
                feedItem.getParent().getId(), feedItem.getFeedPosition()
        ));
        feedItem.getChildren().stream().forEach(this::toSql);
    }


    private static class FeedBuilder {
        List<FeedItem> items = new ArrayList<>();


        public FeedBuilder addItem(FeedItem item) {
            items.add(item);
            return this;
        }

        public List<FeedItem> build() {
            return items;
        }
    }

    private static class FeedItemBuilder {
        FeedItem item = new FeedItem();
        FeedItemBuilder parent;

        public FeedItemBuilder(FeedItemBuilder parent) {
            this.parent = parent;
            if (parent != null)
                item.setParent(parent.item);
        }

        public FeedItemBuilder newItem() {
            return new FeedItemBuilder(this);
        }

        public FeedItemBuilder id(int id) {
            item.setId(i(id));
            return this;
        }

        private String i(int id) {
            return ("0000000000000000000000000000000000000" + id).replaceAll(".*(\\d{32})$", "$1");
        }

        public FeedItemBuilder __() {
            return this;
        }

        public FeedItemBuilder build() {
            List<FeedItem> children = this.parent.item.getChildren();
            if (children == null) {
                children = new ArrayList<>();
                this.parent.item.setChildren(children);
            }
            children.add(item);
            return parent;
        }

        public FeedItemBuilder type(FeedItem.EntityType type) {
            item.setEntityType(type);
            return this;
        }

        public FeedItemBuilder img(String img) {
            item.setImg(img);
            return this;
        }

        public FeedItemBuilder title(String title) {
            item.setTitle(title);
            return this;
        }

        public FeedItemBuilder entityId(int i) {
            item.setEntity(i(i));
            return this;
        }

        public FeedItemBuilder position(FeedItem.FeedPosition feedPosition) {
            item.setFeedPosition(feedPosition);
            return this;
        }

        public FeedItemBuilder entityId(String entityId) {
            item.setEntity(entityId);
            return this;
        }

        public FeedItemBuilder feed(String feedId) {
            item.setFeedId(feedId);
            return this;
        }

        public FeedItemBuilder score(double score) {
            item.setScore(score);
            return this;
        }
    }
}
package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.introspect.Annotated;
import com.fasterxml.jackson.databind.introspect.JacksonAnnotationIntrospector;
import com.fasterxml.jackson.databind.introspect.ObjectIdInfo;

import java.util.ArrayList;

public class JsonIdTest {
    //    @Test
    public void testJsonId() throws JsonProcessingException {
        ObjectMapper mapper = new ObjectMapper();
        mapper.setSerializationInclusion(JsonInclude.Include.NON_NULL);
        mapper.setAnnotationIntrospector(new JacksonAnnotationIntrospector() {
            @Override
            public ObjectIdInfo findObjectIdInfo(Annotated ann) {
                if (ann.getRawType() == FeedItem.class) {
                    return null;
                }
                return super.findObjectIdInfo(ann);
            }

            @Override
            public ObjectIdInfo findObjectReferenceInfo(Annotated ann, ObjectIdInfo objectIdInfo) {
                return super.findObjectReferenceInfo(ann, objectIdInfo);
            }
        });

        FeedItem item1 = new FeedItem();
        FeedItem parent = new FeedItem();
        parent.setId("abc");
        item1.setProperty("p1", parent);
//        item1.setParent(parent);
        FeedItem item2 = new FeedItem();
//        item2.setParent(parent);
        item2.setProperty("p2", parent);

//        System.out.println(mapper.writeValueAsString(item1));
        ArrayList list = new ArrayList() {{
            add(item1);
            add(item2);
        }};
        JsonNode jsonNode = mapper.valueToTree(list);

        ObjectMapper mapper1 = new ObjectMapper();
    }


}

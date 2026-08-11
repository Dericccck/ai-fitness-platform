//package com.shuyiwa.frog.core.domain;
//
//import com.fasterxml.jackson.databind.DeserializationFeature;
//import com.fasterxml.jackson.databind.ObjectMapper;
//import org.junit.Test;
//import org.junit.runner.RunWith;
//import org.springframework.beans.factory.annotation.Autowired;
//import org.springframework.boot.test.context.SpringBootTest;
//import org.springframework.test.context.ActiveProfiles;
//import org.springframework.test.context.junit4.SpringRunner;
//
//import java.io.IOException;
//
//@RunWith(SpringRunner.class)
//@ActiveProfiles(profiles = "dev")
//@SpringBootTest
//public class EntityIdResolverTest {
//    @Autowired
//    ObjectMapper mapper;
////    @Test
////    public void contextLoads() throws IOException {
////        Contestant contestant = new Contestant();
////        Organization organization = new Organization();
////        organization.setId("aaa");
////        contestant.setOrganization(organization);
//////        mapper.configure(DeserializationFeature.FAIL_ON_UNRESOLVED_OBJECT_IDS, false);
////        String content = mapper.writeValueAsString(contestant);
////        System.out.println(content);
////        content=content.replace("\"organization\":\"aaa\"","\"organization\":{\"id\":\"aaabb\"}");
////        System.out.println(content);
////        Contestant contestant1 = mapper.readValue(content, Contestant.class);
////        System.out.println(contestant1.getOrganization().getId());
////    }
//
//    @Test
//    public void test() throws IOException {
//        mapper.configure(DeserializationFeature.FAIL_ON_UNRESOLVED_OBJECT_IDS, false);
//        String s = "{\"key\":\"1548740693378.0.38516844816256035\",\"contestantType\":\"INDIVIDUAL\",\"signInfoList\":[],\"worksList\":[{\"key\":\"1548740693378.0.06800722945780957\",\"name\":\"Samsung A9s 热播综艺15s 原版 (1) (1)\",\"loginUserFile\":{\"id\":\"40289f8168981e7e0168981f4ccc0000\",\"name\":\"file\",\"originalFilename\":\"Samsung A9s 热播综艺15s 原版 (1) (1).mp4\",\"suffix\":\".mp4\",\"contentType\":\"video/mp4\",\"size\":4100243,\"diskUrl\":\"https://frog-console.shuyiwa.com/pass/disk/upload/2019-01-29\\\\40289f8168981e7e0168981f4ccc0000.mp4\",\"path\":\"D:\\\\work\\\\shuyiwa\\\\fitness.backend\\\\upload-dir\\\\2019-01-29\\\\40289f8168981e7e0168981f4ccc0000.mp4\",\"removed\":false,\"useType\":\"WORKS\",\"createTime\":1548740414000,\"sampleFileName\":\"Samsung A9s 热播综艺15s 原版 (1) (1)\"},\"loginUserFileId\":\"40289f8168981e7e0168981f4ccc0000\"}],\"name\":\"CES\",\"sex\":\"MAN\",\"age\":\"22\",\"contestItem\":{\"id\":\"00000000000000000000000000000001\"},\"agentPhone\":\"13333333333\",\"agentName\":\"\",\"agentSex\":\"MAN\",\"agentRelation\":\"EEDDD\",\"agentAddress\":\"\"}";
//
//        Contestant[] contestant = mapper.readValue("["+s+","+s+"]", Contestant[].class);
//        System.out.println(contestant[0].getContestItem().getId());
//    }
//}

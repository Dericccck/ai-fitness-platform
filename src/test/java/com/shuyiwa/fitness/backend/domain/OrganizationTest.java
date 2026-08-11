//package com.shuyiwa.frog.core.domain;
//
//import com.shuyiwa.frog.core.domain.dict.ContestantType;
//import org.junit.Test;
//
//import java.util.ArrayList;
//import java.util.List;
//
//public class OrganizationTest {
//    @Test
//    public void generateInitDate() {
//        newOrganization()
//                .setId("00000000000000000000000000000003")
//                .setName("树艺🐸培训")
//                .addContestant()
//                .__().setId("00000000000000000000000000030001")
//                .__().setAgentPhone("123456001")
//                .__().setName("大娃")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030001")
//                .__().__().setName("青花瓷")
//                .__().__().vote(23)
//                .__().__().like(230)
//                .__().__().build()
//                .__().build()
//                .addContestant()
//                .__().setId("00000000000000000000000000030002")
//                .__().setAgentPhone("123456001")
//                .__().setName("二娃")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030002")
//                .__().__().setName("依然范特西")
//                .__().__().vote(25)
//                .__().__().like(230)
//                .__().__().build()
//                .__().build()
//                .addContestant()
//                .__().setId("00000000000000000000000000030003")
//                .__().setAgentPhone("123456001")
//                .__().setName("三娃")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030003")
//                .__().__().setName("简单爱")
//                .__().__().vote(23)
//                .__().__().like(240)
//                .__().__().build()
//                .__().build()
//                .addContestant()
//                .__().setId("00000000000000000000000000030004")
//                .__().setAgentPhone("123456001")
//                .__().setName("四娃")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030004")
//                .__().__().setName("范特西")
//                .__().__().vote(23)
//                .__().__().like(220)
//                .__().__().build()
//                .__().build()
//                .addContestant()
//                .__().setId("00000000000000000000000000030005")
//                .__().setAgentPhone("123456001")
//                .__().setName("五娃")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030005")
//                .__().__().setName("开不了口")
//                .__().__().vote(23)
//                .__().__().like(220)
//                .__().__().build()
//                .__().build()
//                .addContestant()
//                .__().setId("00000000000000000000000000030006")
//                .__().setAgentPhone("123456001")
//                .__().setName("六娃")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030006")
//                .__().__().setName("头文字D")
//                .__().__().vote(23)
//                .__().__().like(220)
//                .__().__().build()
//                .__().build()
//                .addContestant()
//                .__().setId("00000000000000000000000000030007")
//                .__().setAgentPhone("123456001")
//                .__().setName("七娃")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030007")
//                .__().__().setName("八度空间")
//                .__().__().vote(23)
//                .__().__().like(220)
//                .__().__().build()
//                .__().build()
//                .addContestant()
//                .__().setId("00000000000000000000000000030008")
//                .__().setAgentPhone("123456001")
//                .__().setName("jay")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030008")
//                .__().__().setName("jay")
//                .__().__().vote(23)
//                .__().__().like(220)
//                .__().__().build()
//                .__().build()
//                .addContestant()
//                .__().setId("00000000000000000000000000030009")
//                .__().setAgentPhone("123456001")
//                .__().setName("我很忙")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030009")
//                .__().__().setName("我很忙")
//                .__().__().vote(23)
//                .__().__().like(220)
//                .__().__().build()
//                .__().build()
//                .addContestant()
//                .__().setId("00000000000000000000000000030010")
//                .__().setAgentPhone("123456001")
//                .__().setName("肖邦")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030010")
//                .__().__().setName("11月的肖邦")
//                .__().__().vote(23)
//                .__().__().like(220)
//                .__().__().build()
//                .__().build()
//                .addContestant()
//                .__().setId("00000000000000000000000000030010")
//                .__().setAgentPhone("123456001")
//                .__().setName("秘密")
//                .__().setAge(11)
//                .__().addWorks()
//                .__().__().setId("00000000000000000000000000030010")
//                .__().__().setName("不能说的秘密")
//                .__().__().vote(23)
//                .__().__().like(220)
//                .__().__().build()
//                .__().build()
//                .printSql();
//        ;
//
//
//    }
//
//    private OrganizationBuilder newOrganization() {
//        return new OrganizationBuilder();
//    }
//
//    private static class OrganizationBuilder {
//        Organization organization = new Organization();
//        List<ContestantBuilder> contestantList = new ArrayList<>();
//
//        public OrganizationBuilder setId(String id) {
//            organization.setId(id);
//            return this;
//        }
//
//        public OrganizationBuilder setName(String name) {
//            organization.setName(name);
//            return this;
//        }
//
//        public ContestantBuilder addContestant() {
//            return new ContestantBuilder(this);
//        }
//
//        public void printSql() {
//            System.out.println("insert ignore into organization (id, name, logo, summary) " +
//                    "values ('" + organization.getId() + "', '" + organization.getName() + "'" +
//                    ",'https://picsum.photos/512/512?orgizationId=" + organization.getId() + "'" +
//                    ",'简介\\n简介....\\n简介....');");
//            for (ContestantBuilder contestantBuilder : contestantList) {
//                contestantBuilder.printSql();
//            }
//        }
//
//        private static class ContestantBuilder {
//
//            private OrganizationBuilder organizationBuilder;
//            private Contestant contestant;
//            private List<WorksBuilder> worksBuilders = new ArrayList<>();
//
//            public ContestantBuilder(OrganizationBuilder organizationBuilder) {
//                contestant = new Contestant();
//                contestant.setContestantType(ContestantType.INDIVIDUAL);
//                contestant.setOrganization(organizationBuilder.organization);
//                organizationBuilder.contestantList.add(this);
//                this.organizationBuilder = organizationBuilder;
//            }
//
//            public ContestantBuilder setId(String id) {
//                contestant.setId(id);
//                return this;
//            }
//
//            public ContestantBuilder setAgentPhone(String agentPhone) {
//                contestant.setAgentPhone(agentPhone);
//                return this;
//            }
//
//            public ContestantBuilder setName(String name) {
//                contestant.setName(name);
//                return this;
//            }
//
//            public ContestantBuilder setAge(int age) {
//                contestant.setAge(age);
//                return this;
//            }
//
//            public WorksBuilder addWorks() {
//                return new WorksBuilder(this);
//            }
//
//            public ContestantBuilder __() {
//                return this;
//            }
//
//            public OrganizationBuilder build() {
//                return organizationBuilder;
//            }
//
//            public void printSql() {
//                System.out.println("insert ignore into contestant (id,contestant_type,name,age,contest_item_id,organization_id,best_works_id,deleted) " +
//                        "values ('" + contestant.getId() + "', '" + contestant.getContestantType() + "','" + contestant.getName() + "'" +
//                        "," + contestant.getAge() + ",'00000000000000000000000000000002','" + contestant.getOrganization().getId() + "'" +
//                        ",'" + contestant.getBestWorks().getId() + "',false);");
//                for (WorksBuilder worksBuilder : worksBuilders) {
//                    worksBuilder.printSql();
//                }
//            }
//
//            private static class WorksBuilder {
//                Works works = new Works();
//                private ContestantBuilder contestantBuilder;
//
//                public WorksBuilder(ContestantBuilder contestantBuilder) {
//                    this.contestantBuilder = contestantBuilder;
//                    contestantBuilder.worksBuilders.add(this);
//                    works.setFormat(Works.WorksFormat.IMG);
//                    works.setStatus(Works.WorksStatus.SUCCEEDED);
//                    works.setContestant(contestantBuilder.contestant);
//                }
//
//                public WorksBuilder __() {
//                    return this;
//                }
//
//                public WorksBuilder setId(String id) {
//                    works.setId(id);
//                    works.setCoverUrl("https://picsum.photos/512/512?worksId=" + id);
//                    return this;
//                }
//
//                public WorksBuilder setName(String name) {
//                    works.setName(name);
//                    return this;
//                }
//
//                public ContestantBuilder build() {
//                    contestantBuilder.contestant.setBestWorks(contestantBuilder.worksBuilders.stream().sorted((w1, w2) -> {
//                        int compare = Long.compare(w2.works.getVotes(), w1.works.getVotes());
//                        if (compare == 0) {
//                            compare = Long.compare(w2.works.getUserLikes(), w1.works.getUserLikes());
//                            if (compare == 0) {
//                                compare = w2.works.getId().compareTo(w1.works.getId());
//                            }
//                        }
//                        return compare;
//                    }).findFirst().get().works);
//                    return contestantBuilder;
//                }
//
//                public void printSql() {
//                    System.out.println("insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) " +
//                            "values('" + works.getId() + "', '" + works.getFormat() + "','" + works.getName() + "','" + works.getContestant().getId() + "'" +
//                            ", '" + works.getStatus() + "', '" + works.getCoverUrl() + "'," + works.getUserLikes() + "," + works.getVotes() + "" +
//                            "," + System.currentTimeMillis() + ",false);");
//                }
//
//                public WorksBuilder vote(int vote) {
//                    works.setVotes(vote);
//                    return this;
//                }
//
//                public WorksBuilder like(int like) {
//                    works.setUserLikes(like);
//                    return this;
//                }
//            }
//        }
//    }
//}

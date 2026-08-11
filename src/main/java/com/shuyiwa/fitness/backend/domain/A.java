//package com.shuyiwa.frog.core.domain;
//
//import com.fasterxml.jackson.annotation.JsonIgnore;
//import com.shuyiwa.frog.core.domain.listerner.C;
//import org.hibernate.annotations.GenericGenerator;
//import org.springframework.context.annotation.Lazy;
//
//import javax.persistence.*;
//import java.util.ArrayList;
//import java.util.List;
//
//@Entity
//public class A {
//    @Id
//    @Column(length = 32)
//    @GeneratedValue(generator = "system-uuid")
//    @GenericGenerator(name = "system-uuid", strategy = "uuid")
//    private String id;
//
//
//    @OneToMany
//    @JoinColumn(name = "a_id")
//    @Lazy
//    @JsonIgnore
//    private List<C> cList = new ArrayList<>();
//
//    public String getId() {
//        return id;
//    }
//
//    public void setId(String id) {
//        this.id = id;
//    }
//
//    public List<C> getcList() {
//        return cList;
//    }
//
//    public void setcList(List<C> cList) {
//        this.cList = cList;
//    }
//}

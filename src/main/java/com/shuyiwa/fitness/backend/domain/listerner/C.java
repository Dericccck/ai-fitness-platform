//package com.shuyiwa.frog.core.domain.listerner;
//
//import com.fasterxml.jackson.annotation.JsonIdentityInfo;
//import com.fasterxml.jackson.annotation.JsonIdentityReference;
//import com.fasterxml.jackson.annotation.ObjectIdGenerators;
//import com.shuyiwa.frog.core.domain.A;
//import com.shuyiwa.frog.core.domain.EntityIdResolver;
//import org.hibernate.annotations.GenericGenerator;
//
//import javax.persistence.*;
//
//
//@JsonIdentityInfo(generator = ObjectIdGenerators.PropertyGenerator.class, property = "id", resolver = EntityIdResolver.class, scope = C.class)
//@Entity
//public class C {
//    @Id
//    @Column(length = 32)
//    @GeneratedValue(generator = "system-uuid")
//    @GenericGenerator(name = "system-uuid", strategy = "uuid")
//    private String id;
//
//
//    @JsonIdentityReference(alwaysAsId = true)
//    @ManyToOne
//    @JoinColumn(name = "a_id")
//    private A a;
//
//    public String getId() {
//        return id;
//    }
//
//    public void setId(String id) {
//        this.id = id;
//    }
//
//    public A getA() {
//        return a;
//    }
//
//    public void setA(A a) {
//        this.a = a;
//    }
//}

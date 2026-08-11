package com.shuyiwa.fitness.backend.conf.doc;

import java.lang.annotation.*;

@Target({ElementType.METHOD, ElementType.PARAMETER})
@Retention(RetentionPolicy.RUNTIME)
@Documented
public @interface RuntimeDoc {
    String desc();

    String deprecated() default "";

    String since() default "";

    String sinceTime() default "";
    String deprecatedTime() default "";

    Client[] client() default {Client.Console};

    enum Client {
        Api, Console, Org, Tool
    }
}

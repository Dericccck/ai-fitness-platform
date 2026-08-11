package com.shuyiwa.fitness.backend.conf;

import java.lang.annotation.*;

@Target({ElementType.METHOD, ElementType.PARAMETER,ElementType.FIELD})
@Retention(RetentionPolicy.RUNTIME)
@Documented
@Inherited
public @interface CacheParam {
    String name() default "";
}

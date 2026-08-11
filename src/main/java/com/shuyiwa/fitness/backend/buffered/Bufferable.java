package com.shuyiwa.fitness.backend.buffered;

import java.lang.annotation.*;

@Target({ElementType.METHOD, ElementType.TYPE})
@Retention(RetentionPolicy.RUNTIME)
@Inherited
@Documented
public @interface Bufferable {
    int permits() default 1;

    String name() default "";
}

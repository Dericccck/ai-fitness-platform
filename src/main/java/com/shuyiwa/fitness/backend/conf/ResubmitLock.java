package com.shuyiwa.fitness.backend.conf;

import java.lang.annotation.*;
import java.util.concurrent.TimeUnit;

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
@Documented
@Inherited
public @interface ResubmitLock {
    //redis锁前缀
    String prefix() default "";
    //redis锁过期时间
    int expire() default 5;
    //redis锁过期时间单位
    TimeUnit timeUnit() default TimeUnit.SECONDS;
    //redis  key分隔符
    String delimiter() default ":";
}

package com.shuyiwa.fitness.backend.buffered;

import org.springframework.context.annotation.AdviceMode;
import org.springframework.context.annotation.Import;

import java.lang.annotation.*;

@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Documented
@Import(BufferingConfigurationSelector.class)
public @interface EnableBuffering {
    AdviceMode mode() default AdviceMode.PROXY;
}

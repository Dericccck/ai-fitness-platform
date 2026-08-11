package com.shuyiwa.fitness.backend.conf;

import org.aspectj.lang.ProceedingJoinPoint;

public interface CacheKeyGenerator {
    //获取AOP参数,生成指定缓存Key
    String getLockKey(ProceedingJoinPoint joinPoint);
}

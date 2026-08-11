package com.shuyiwa.fitness.backend.conf;

import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.util.ReflectionUtils;
import org.springframework.util.StringUtils;

import java.lang.annotation.Annotation;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Parameter;


public class CacheKeyGeneratorImp implements CacheKeyGenerator{
    @Override
    public String getLockKey(ProceedingJoinPoint joinPoint) {
        //获取连接点的方法签名对象
        MethodSignature methodSignature = (MethodSignature) joinPoint.getSignature();
        //Method对象
        Method method = methodSignature.getMethod();
        //获取Method对象上的注解对象
        ResubmitLock resubmitLock = method.getAnnotation(ResubmitLock.class);
        //获取方法参数
        final Object[] args = joinPoint.getArgs();
        //获取Method对象上所有的注解
        final Parameter[] parameters = method.getParameters();
        StringBuilder sb = new StringBuilder();
        for(int i=0;i<parameters.length;i++){
            final CacheParam cacheParams = parameters[i].getAnnotation(CacheParam.class);
            //如果属性不是CacheParam注解，则不处理
            if(cacheParams == null){
                continue;
            }
            //如果属性是CacheParam注解，则拼接 连接符（：）+ CacheParam
            sb.append(resubmitLock.delimiter()).append(args[i]);
        }
        //如果方法上没有加CacheParam注解
        if(StringUtils.isEmpty(sb.toString())){
            //获取方法上的多个注解（为什么是两层数组：因为第二层数组是只有一个元素的数组）
            final Annotation[][] parameterAnnotations = method.getParameterAnnotations();
            //循环注解
            for(int i=0;i<parameterAnnotations.length;i++){
                final Object object = args[i];
                //获取注解类中所有的属性字段
                final Field[] fields = object.getClass().getDeclaredFields();
                for(Field field : fields){
                    //判断字段上是否有CacheParam注解
                    final CacheParam annotation = field.getAnnotation(CacheParam.class);
                    //如果没有，跳过
                    if(annotation ==null){
                        continue;
                    }
                    //如果有，设置Accessible为true（为true时可以使用反射访问私有变量，否则不能访问私有变量）
                    field.setAccessible(true);
                    //如果属性是CacheParam注解，则拼接 连接符（：）+ CacheParam
                    sb.append(resubmitLock.delimiter()).append(ReflectionUtils.getField(field,object));
                }
            }
        }
        //返回指定前缀的key
        return resubmitLock.prefix() + sb.toString();

    }
}

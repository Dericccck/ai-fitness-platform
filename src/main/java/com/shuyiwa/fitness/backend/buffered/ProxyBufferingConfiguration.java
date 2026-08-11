package com.shuyiwa.fitness.backend.buffered;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.aopalliance.aop.Advice;
import org.aopalliance.intercept.MethodInterceptor;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.aop.Pointcut;
import org.springframework.aop.support.AbstractBeanFactoryPointcutAdvisor;
import org.springframework.aop.support.StaticMethodMatcherPointcut;
import org.springframework.beans.factory.BeanFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.config.BeanDefinition;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Role;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Semaphore;

@Configuration
@Role(BeanDefinition.ROLE_INFRASTRUCTURE)
public class ProxyBufferingConfiguration {
    private static final Log logger = LogFactory.getLog(ProxyBufferingConfiguration.class);
    ConcurrentHashMap<Bufferable, Semaphore> map = new ConcurrentHashMap<>();
    @Autowired
    BeanFactory beanFactory;
    @Autowired
    ObjectMapper mapper;
    @Autowired
    RedisBufferService redisBufferService;

    @Bean(name = "buffer.org.springframework.buffer.config.internalCacheAdvisor")
    @Role(BeanDefinition.ROLE_INFRASTRUCTURE)
    public BeanFactoryBufferOperationSourceAdvisor cacheAdvisor() {
        BeanFactoryBufferOperationSourceAdvisor advisor = new BeanFactoryBufferOperationSourceAdvisor();
        advisor.setAdvice(bufferInterceptor());
        return advisor;
    }

    private Advice bufferInterceptor() {
        return (MethodInterceptor) invocation -> {
            Method method = invocation.getMethod();
            Bufferable bufferable = method.getAnnotation(Bufferable.class);
            if (redisBufferService.isFromBuffer()) {
                return invocation.proceed();
            } else {
                Semaphore semaphore = map.computeIfAbsent(bufferable, k -> new Semaphore(bufferable.permits()));
                if (semaphore.tryAcquire()) {
                    try {
                        return invocation.proceed();
                    } finally {
                        semaphore.release();
                    }
                } else {//
                    BufferedItem item = new BufferedItem();
                    item.setBeanClass(method.getDeclaringClass());
                    List<BufferedItemArgument> arguments = new ArrayList<>();
                    int count = method.getParameterCount();
                    Class<?>[] types = method.getParameterTypes();
                    Object[] invocationArguments = invocation.getArguments();
                    for (int i = 0; i < count; i++) {
                        BufferedItemArgument argument = new BufferedItemArgument();
                        argument.setC(types[i]);
                        argument.setV(invocationArguments[i]);
                        arguments.add(argument);
                    }
                    item.setArguments(arguments);
                    item.setMethod(invocation.getMethod().getName());
                    redisBufferService.push(item);
                    return null;
                }
            }
        };
    }

    public static class BufferedItemArgument {
        private Class<?> c;
        private Object v;

        public Class<?> getC() {
            return c;
        }

        public void setC(Class<?> c) {
            this.c = c;
        }

        public Object getV() {
            return v;
        }

        public void setV(Object v) {
            this.v = v;
        }
    }

    public static class BufferedItem {
        private Class beanClass;
        private List<BufferedItemArgument> arguments;
        private String method;

        public String getMethod() {
            return method;
        }

        public void setMethod(String method) {
            this.method = method;
        }

        public Class getBeanClass() {
            return beanClass;
        }

        public void setBeanClass(Class beanClass) {
            this.beanClass = beanClass;
        }

        public List<BufferedItemArgument> getArguments() {
            return arguments;
        }

        public void setArguments(List<BufferedItemArgument> arguments) {
            this.arguments = arguments;
        }
    }

}

class BeanFactoryBufferOperationSourceAdvisor extends AbstractBeanFactoryPointcutAdvisor {
    @Override
    public Pointcut getPointcut() {
        return new StaticMethodMatcherPointcut() {
            @Override
            public boolean matches(Method method, Class<?> targetClass) {
                Bufferable annotation = method.getAnnotation(Bufferable.class);
                return annotation != null;
            }
        };
    }

}

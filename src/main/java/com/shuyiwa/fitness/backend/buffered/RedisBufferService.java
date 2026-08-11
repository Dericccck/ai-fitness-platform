package com.shuyiwa.fitness.backend.buffered;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.BeanFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import javax.annotation.PostConstruct;
import java.lang.reflect.Method;
import java.util.*;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

@Service
@Lazy
public class RedisBufferService {
    public static final String BUFFER = "_buffer";
    private static final Log logger = LogFactory.getLog(RedisBufferService.class);
    @Autowired(required = false)
    StringRedisTemplate redisTemplate;
    @Autowired
    ObjectMapper mapper;
    @Autowired
    BeanFactory beanFactory;
    private ThreadLocal<Boolean> isFromBuffer = new ThreadLocal<>();

    public void push(ProxyBufferingConfiguration.BufferedItem bufferedItem) {
        try {
            String buffer = mapper.writeValueAsString(bufferedItem);
            if (redisTemplate != null) {
                redisTemplate.opsForList().leftPush(BUFFER, buffer);
            }
            logger.warn("append buffer:" + buffer);
        } catch (JsonProcessingException e) {
            logger.warn("write buffer exception", e);
        }
    }

    @PostConstruct
    void init() {
        if (redisTemplate != null) {
            ScheduledExecutorService executor = Executors.newScheduledThreadPool(1);
            executor.scheduleAtFixedRate(() -> {

                Map<Method, Map.Entry<Object, List<Object>>> methodRowsMap = new HashMap<>();
                for (int i = 0; i < 512; i++) {
                    boolean isLast = false;
                    String buffer = redisTemplate.opsForList().leftPop(BUFFER);
                    if (buffer == null) {
                        isLast = true;
                        buffer = redisTemplate.opsForList().leftPop(BUFFER, 10, TimeUnit.SECONDS);
                    }
                    if (buffer != null) {
                        logger.info("read buffer:" + buffer);
                        try {
                            ProxyBufferingConfiguration.BufferedItem item = mapper.readValue(buffer, ProxyBufferingConfiguration.BufferedItem.class);
                            Object bean = beanFactory.getBean(item.getBeanClass());
                            List<ProxyBufferingConfiguration.BufferedItemArgument> itemArguments = item.getArguments();
                            Method method = item.getBeanClass().getMethod(item.getMethod(), itemArguments.stream().map(a -> a.getC()).toArray(j -> new Class<?>[j]));

                            Object[] arguments = new Object[itemArguments.size()];
                            for (int j = 0; j < arguments.length; j++) {
                                ProxyBufferingConfiguration.BufferedItemArgument argument = itemArguments.get(j);
                                arguments[j] = mapper.readValue(mapper.writeValueAsString(argument.getV()), argument.getC());
                            }
                            Method batchMethod = getBatchMethod(itemArguments.size(), method, item.getBeanClass());
                            if (batchMethod != null) {
                                methodRowsMap.computeIfAbsent(batchMethod, k -> new AbstractMap.SimpleEntry<>(bean, new ArrayList<>())).getValue().add(arguments[0]);
                            } else {
                                isFromBuffer.set(true);
                                try {
                                    method.invoke(bean, arguments);
                                } finally {
                                    isFromBuffer.remove();
                                }
                            }
                        } catch (Exception e) {
                            logger.warn("read buffer exception:buffer:" + buffer, e);
                        }
                        if (isLast) {
                            if (i != 0) {
                                logger.info("read buffer:break:isLast:" + i);
                            }
                            break;
                        }
                    } else {
                        if (i != 0) {
                            logger.info("read buffer:break:" + i);
                        }
                        break;
                    }
                }


                for (Map.Entry<Method, Map.Entry<Object, List<Object>>> entry : methodRowsMap.entrySet()) {
                    try {
                        isFromBuffer.set(true);
                        entry.getKey().invoke(entry.getValue().getKey(), entry.getValue().getValue());
                        try {
                        } finally {
                            isFromBuffer.remove();
                        }
                    } catch (Exception e) {
                        logger.warn("read buffer exception:batchMethod:" + entry.getKey(), e);
                    }
                }

            }, 120, 2, TimeUnit.SECONDS);
        }
    }

    private Method getBatchMethod(int itemArguments, Method method, Class beanClass) {
        if (itemArguments != 1) {
            return null;
        }
        String name = method.getAnnotation(Bufferable.class).name();
        if (!StringUtils.isEmpty(name)) {
            for (Method otherMethod : beanClass.getDeclaredMethods()) {
                if (otherMethod.isAnnotationPresent(BatchBufferWorker.class)) {
                    BatchBufferWorker worker = otherMethod.getAnnotation(BatchBufferWorker.class);
                    if (worker.name().equals(name)) {
                        return otherMethod;
                    }
                }
            }
        }
        return null;
    }


    public boolean isFromBuffer() {
        return isFromBuffer.get() != null;
    }

}

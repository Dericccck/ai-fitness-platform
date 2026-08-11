package com.shuyiwa.fitness.backend.conf;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.BeansException;
import org.springframework.context.ApplicationContext;
import org.springframework.context.ApplicationContextAware;
import org.springframework.stereotype.Component;

@Component
public class ContextProvider implements ApplicationContextAware {
    private static final Log logger = LogFactory.getLog(ContextProvider.class);
    private static ApplicationContext CONTEXT;

    public static <T> T getBean(Class<T> beanClass) {
        if (CONTEXT == null) {
            logger.warn("getBean but context is null");
            return null;
        }
        return CONTEXT.getBean(beanClass);
    }

    @Override
    public void setApplicationContext(ApplicationContext applicationContext) throws BeansException {
        logger.info("setApplicationContext:" + applicationContext);
        CONTEXT = applicationContext;
    }
}

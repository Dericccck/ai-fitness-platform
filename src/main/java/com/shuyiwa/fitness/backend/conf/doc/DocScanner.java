package com.shuyiwa.fitness.backend.conf.doc;

import com.shuyiwa.fitness.backend.service.DocService;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.BeansException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationContext;
import org.springframework.context.ApplicationContextAware;
import org.springframework.context.annotation.Configuration;

import java.lang.reflect.Method;

/**
 * 为什么不实用rest-doc？ 因为rest-doc比较麻烦，UT驱动生成静态文件，再集成到web项目或放到其他web容器里才行，给不同到客户端生成不同的文档也很麻烦
 * 我们这么做的缺点是:1.需要占用运行时内存;2.无法读取注释或参数名
 * 目前已知的缺点可以接受
 */
@Configuration
public class DocScanner implements ApplicationContextAware {
    private static final Log logger = LogFactory.getLog(DocScanner.class);

    @Autowired
    DocService docService;

    @Override
    public void setApplicationContext(ApplicationContext applicationContext) throws BeansException {
        for (String beanName : applicationContext.getBeanDefinitionNames()) {
            Object obj = applicationContext.getBean(beanName);
            Class<?> objClz = obj.getClass();
            if (org.springframework.aop.support.AopUtils.isAopProxy(obj)) {
                objClz = org.springframework.aop.support.AopUtils.getTargetClass(obj);
            }

            for (Method m : objClz.getDeclaredMethods()) {
                if (m.isAnnotationPresent(RuntimeDoc.class)) {
                    docService.add(m.getAnnotation(RuntimeDoc.class), m);
                }
            }
        }
    }
}

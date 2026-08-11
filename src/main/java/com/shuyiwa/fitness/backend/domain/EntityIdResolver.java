package com.shuyiwa.fitness.backend.domain;

import com.fasterxml.jackson.annotation.ObjectIdGenerator;
import com.fasterxml.jackson.annotation.ObjectIdResolver;
import com.fasterxml.jackson.annotation.SimpleObjectIdResolver;
import com.shuyiwa.fitness.backend.conf.ContextProvider;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.cglib.core.ReflectUtils;

import javax.persistence.EntityManager;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

public class EntityIdResolver extends SimpleObjectIdResolver {
    private static final Log logger = LogFactory.getLog(EntityIdResolver.class);
    private EntityManager entityManager;

    public EntityIdResolver(
            final EntityManager entityManager) {
        this.entityManager = entityManager;
    }

    public EntityIdResolver() {
        this.entityManager = ContextProvider.getBean(EntityManager.class);
    }

    @Override
    public void bindItem(ObjectIdGenerator.IdKey id, Object ob) {
        try {
            super.bindItem(id, ob);
        } catch (IllegalStateException e) {
            logger.warn(e.getMessage());
        }
    }

    @Override
    public Object resolveId(final ObjectIdGenerator.IdKey id) {
        Object object = super.resolveId(id);
        if (object == null) {
            if (entityManager != null) {
                try {
                    object = this.entityManager.find(id.scope, id.key);
                } catch (Exception e) {
                    logger.info("resolveId exception", e);
                }

            }
            if (object == null) {
                object = ReflectUtils.newInstance(id.scope);
                try {
                    Method setId = id.scope.getMethod("setId", String.class);
                    setId.invoke(object, id.key);
                } catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException e) {
                    logger.warn("set id exception", e);
                }
            }
        }
        return object;
    }


    @Override
    public ObjectIdResolver newForDeserialization(final Object context) {
        return this;
    }

}

package com.shuyiwa.fitness.backend.service;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.shuyiwa.fitness.backend.conf.RequestBodyPartResolverConfig;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.*;

import javax.persistence.Transient;
import java.lang.reflect.Method;
import java.util.*;
import java.util.function.Consumer;
import java.util.stream.Collectors;

@Service
public class DocService {
    private Map<RuntimeDoc.Client, List<Method>> methodsMap = new HashMap<>();

    public void add(RuntimeDoc runtimeDoc, Method method) {
        Arrays.stream(runtimeDoc.client()).forEach(client ->
                methodsMap.computeIfAbsent(client, k -> new ArrayList<>()).add(method)
        );

    }

    public List<DocItem> getDocItems(RuntimeDoc.Client client) {
        return methodsMap.computeIfAbsent(client, k -> new ArrayList<>()).stream()
                .map(this::method2DocItem)
                .filter(Objects::nonNull)
                .collect(Collectors.toList());

    }

    private DocItem method2DocItem(Method method) {
        DocItem docItem = new DocItem();
        RuntimeDoc runtimeDoc = method.getAnnotation(RuntimeDoc.class);
        PreAuthorize preAuthorize = method.getAnnotation(PreAuthorize.class);
        if (preAuthorize != null) {
            String value = preAuthorize.value();
            value = value.replace("isAuthenticated()", "需要登陆");
            docItem.setProperty("auth", value);
        }
        doIfNotEmpty(deprecated -> docItem.setProperty("deprecated", deprecated), runtimeDoc.deprecated());
        doIfNotEmpty(since -> docItem.setProperty("since", since), runtimeDoc.since());
        doIfNotEmpty(sinceTime -> docItem.setProperty("sinceTime", sinceTime), runtimeDoc.sinceTime());
        doIfNotEmpty(deprecatedTime -> docItem.setProperty("deprecatedTime", deprecatedTime), runtimeDoc.deprecatedTime());
        docItem.setProperty("desc", runtimeDoc.desc());
        if (!method.isAnnotationPresent(RequestMapping.class)) {
            return null;
        }
        RequestMapping mapping = method.getAnnotation(RequestMapping.class);
        docItem.setProperty("methodNames", Arrays.stream(mapping.method()).map(m -> m.name()));
        docItem.setProperty("url", mapping.value());
        docItem.setProperty("params",
                Arrays.stream(method.getParameters()).map(parameter -> {
                    Map<String, Object> properties = new HashMap<>();
                    properties.put("name", parameter.getName());
                    properties.put("class", parameter.getType().getName());
                    if (parameter.isAnnotationPresent(RuntimeDoc.class)) {
                        RuntimeDoc annotation = parameter.getAnnotation(RuntimeDoc.class);
                        doIfNotEmpty(desc -> properties.put("desc", desc), annotation.desc());
                    }
                    if (parameter.isAnnotationPresent(RequestBody.class)) {
                        properties.put("channel", RequestBody.class.getSimpleName());
                        RequestBody requestBody = parameter.getAnnotation(RequestBody.class);
                        properties.put("required", requestBody.required());
                        properties.put("defaultValue", null);
                    } else if (parameter.isAnnotationPresent(PathVariable.class)) {
                        properties.put("channel", PathVariable.class.getSimpleName());
                        PathVariable pathVariable = parameter.getAnnotation(PathVariable.class);
                        properties.put("required", pathVariable.required());
                        doIfNotEmpty(name -> properties.put("name", name), pathVariable.name(), pathVariable.value());
                        properties.put("defaultValue", null);
                    } else if (parameter.isAnnotationPresent(RequestBodyPartResolverConfig.RequestBodyPart.class)) {
                        properties.put("channel", RequestBodyPartResolverConfig.RequestBodyPart.class.getSimpleName());
                        RequestBodyPartResolverConfig.RequestBodyPart bodyPart = parameter.getAnnotation(RequestBodyPartResolverConfig.RequestBodyPart.class);
                        doIfNotEmpty(name -> properties.put("name", name), bodyPart.value());
                        properties.put("required", true);
                        properties.put("defaultValue", null);
                    } else if (parameter.isAnnotationPresent(RequestHeader.class)) {
                        RequestHeader header = parameter.getAnnotation(RequestHeader.class);
                        if ("X-Forwarded-For".equals(header.value())) {
                            //ignore
                            return null;
                        }
                    } else if (parameter.isAnnotationPresent(AuthenticationPrincipal.class)) {
                        //ignore
                        return null;
                    } else if (parameter.isAnnotationPresent(RequestParam.class)) {
                        properties.put("channel", RequestParam.class.getSimpleName());
                        RequestParam requestParam = parameter.getAnnotation(RequestParam.class);
                        doIfNotEmpty(name -> properties.put("name", name), requestParam.name(), requestParam.value());
                        if (properties.getOrDefault("name", "").equals("__ip")) {
                            //ignore
                            return null;
                        }
                        properties.put("required", requestParam.required());
                        properties.put("defaultValue", ValueConstants.DEFAULT_NONE.equals(requestParam.defaultValue()) ? null : requestParam.defaultValue());
                    } else {
                        properties.put("channel", RequestParam.class.getSimpleName());
                        properties.put("required", true);
                        properties.put("defaultValue", null);
                    }
                    return properties;
                }).filter(Objects::nonNull).collect(Collectors.toList())
        );
        return docItem;
    }

    private void doIfNotEmpty(Consumer<String> consumer, String... values) {
        for (String value : values) {
            if (!StringUtils.isEmpty(value)) {
                consumer.accept(value);
                break;
            }
        }
    }


    public static class DocItem {

        @Transient
        private Map<String, Object> properties = new HashMap<>();

        @JsonAnyGetter
        public Map<String, Object> getProperties() {
            return properties;
        }

        @JsonAnySetter
        public void setProperty(String name, Object value) {
            properties.put(name, value);
        }

    }
}

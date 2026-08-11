package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.Organization;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.stream.Collectors;

@RestController
public class DictController {

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "获取所有权限")
    @RequestMapping(value = "api/dict/authority", method = RequestMethod.GET)
    Authority[] authority() {
        return Authority.values();
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "获取所有机构类型列表")
    @RequestMapping(value = "api/dict/organization/type", method = RequestMethod.GET)
    List<HashMap> organizationType() {
        return Arrays.stream(Organization.OrganizationType.values()).map(t -> new HashMap() {{
            put("value", t.name());
            put("label", t.getLabel());
        }}).collect(Collectors.toList());
    }

}

package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.service.DocService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
public class DocController {
    @Autowired
    DocService docService;

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "API接口文档")
    @RequestMapping(value = "api/doc/client/api", method = RequestMethod.GET)
    List<DocService.DocItem> clientApi() {
        return docService.getDocItems(RuntimeDoc.Client.Api);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "Console接口文档")
    @RequestMapping(value = "api/doc/client/console", method = RequestMethod.GET)
    List<DocService.DocItem> clientConsole() {
        return docService.getDocItems(RuntimeDoc.Client.Console);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "自用工具接口文档")
    @RequestMapping(value = "api/doc/client/tool", method = RequestMethod.GET)
    List<DocService.DocItem> toolConsole() {
        return docService.getDocItems(RuntimeDoc.Client.Tool);
    }
}

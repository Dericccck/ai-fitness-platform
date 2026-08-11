package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.ArticleRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;

import java.util.Map;

@Controller
public class PageController {
    @Autowired
    ArticleRepository articleRepository;

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "测试文章")
    @GetMapping("/page/article.html")
    public String home(@RequestParam(required = false) String id, Map<String, Object> model) {
        if (!StringUtils.isEmpty(id)) {
            articleRepository.findById(id).ifPresent(article -> {
                model.put("article", article);
            });
        }
        return "page/article";
    }
}

package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.service.LoginUserFileService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@RestController
public class AppStoreController {
    @Autowired
    LoginUserFileService loginUserFileService;


    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "保存app")
    @RequestMapping(value = "api/appStore/save", method = RequestMethod.POST)
    public void save(@RequestParam String loginUserFileId) throws FrogException {
        loginUserFileService.saveToAppStore(loginUserFileId);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "列出所有上传的app")
    @RequestMapping("api/appStore/list")
    public List<Map<String, Object>> list() throws FrogException {
        return loginUserFileService.listAppStore();
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Console, desc = "根据文件名删除app")
    @RequestMapping(value = "api/appStore/deleteByName", method = RequestMethod.POST)
    public boolean delete(String name) throws FrogException {
        return loginUserFileService.deleteFileFromAppStore(name);
    }
}

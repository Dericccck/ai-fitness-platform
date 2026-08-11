package com.shuyiwa.fitness.backend.web;

import com.netflix.appinfo.ApplicationInfoManager;
import com.netflix.appinfo.InstanceInfo;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.concurrent.atomic.AtomicBoolean;

@Controller
@RestController
public class HealthController {
    private static final Log logger = LogFactory.getLog(HealthController.class);
    @Autowired
    ApplicationInfoManager applicationInfoManager;
    private AtomicBoolean shutdown = new AtomicBoolean(false);

    @RuntimeDoc(client = {RuntimeDoc.Client.Tool}, desc = "低消耗的监控检查")
    @RequestMapping(value = "api/health/check", method = {RequestMethod.HEAD, RequestMethod.GET})
    public void check(HttpServletResponse response) throws IOException {
        if (shutdown.get()) {
            logger.warn("already shutdown");
        }
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Tool}, desc = "关闭")
    @RequestMapping(value = "api/health/shutdown", method = {RequestMethod.HEAD, RequestMethod.GET})
    public void shutdown() {
        shutdown.set(true);
        applicationInfoManager.setInstanceStatus(InstanceInfo.InstanceStatus.DOWN);
    }
}

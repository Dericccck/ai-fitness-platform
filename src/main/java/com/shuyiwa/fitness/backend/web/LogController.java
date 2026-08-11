package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.domain.LoginUserRepository;
import com.shuyiwa.fitness.backend.domain.MarketingActionMinute;
import com.shuyiwa.fitness.backend.event.MarketingActionEvent;
import com.shuyiwa.fitness.backend.event.WorksViewEvent;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.event.ArticleViewTimeEvent;
import com.shuyiwa.fitness.backend.event.ItemViewEvent;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.http.MediaType;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.*;

import javax.imageio.ImageIO;
import java.awt.*;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

@Controller
public class LogController {
    public static final String separator = (char) 2 + "";
    private static final Log logger = LogFactory.getLog(LogController.class);
    private static final ConcurrentHashMap<String, Log> loggerMap = new ConcurrentHashMap<>();
    private static byte[] pixel = get1x1PixelImage();
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    private ApplicationEventPublisher applicationEventPublisher;

    public static byte[] get1x1PixelImage() {
        try {
            BufferedImage singlePixelImage = new BufferedImage(1, 1, BufferedImage.TYPE_4BYTE_ABGR);
            Color transparent = new Color(0, 0, 0, 0);
            singlePixelImage.setRGB(0, 0, transparent.getRGB());

            ByteArrayOutputStream baos = new ByteArrayOutputStream();
            ImageIO.write(singlePixelImage, "png", baos);
            byte[] imageInBytes = baos.toByteArray();
            baos.close();

            return imageInBytes;
        } catch (IOException e) {
            logger.error(e);
            throw new RuntimeException(e);
        }
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Api}, desc = "监控日志")
    @RequestMapping(value = "api/img/log/entity/{entityName}/{id}/{action}.png", method = RequestMethod.GET, produces = MediaType.IMAGE_PNG_VALUE)
    @ResponseBody
    public byte[] entityView(
            @RuntimeDoc(desc = "监控日志实体，支持(article,organization,works,activity,frogRank,item,marketing,channel)")
            @PathVariable("entityName") String entityName,
            @PathVariable(value = "id") String id,
            @RuntimeDoc(desc = "监控日志实体，支持(view,click,viewtime)")
            @PathVariable("action") String action,
            @RequestHeader(value = "X-Forwarded-For-Frog-Core", required = false) String coreIp,
            @RequestHeader(value = "User-Frog-Core", required = false) String coreLoginUserId,
            @RequestHeader(value = "X-Forwarded-For", required = false) String[] xf,
            @RequestHeader(value = "User-Agent", required = false) String ua,
            @RequestParam(value = "__ip", required = false) String clientIp,
            @RequestParam(value = "version", required = false) String version,
            @RequestParam(value = "uniqueId", required = false) String uniqueId,
            @RequestParam(value = "os", required = false) String os,
            @RuntimeDoc(desc = "客户端，支持(app,other),分别表示端内和端外")
            @RequestParam(value = "client", required = false, defaultValue = "") String client,
            @RequestParam(value = "contestSeasonId", required = false, defaultValue = "") String contestSeasonId,
            @RequestParam(value = "viewTime", required = false, defaultValue = "0") Long viewTime,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) {
        String loginUserId = Optional.ofNullable(coreLoginUserId).orElse(Optional.ofNullable(frogUserDetails).map(d -> d.getLoginUser(loginUserRepository)).map(loginUser -> loginUser.getId()).orElse(""));
        StringBuilder message = new StringBuilder()
                .append(System.currentTimeMillis())
                .append(separator).append(id)
                .append(separator).append(
                        Optional.ofNullable(coreIp)
                                .orElse(Optional.ofNullable(xf)
                                        .map(x ->
                                                Arrays.stream(x)
                                                        .reduce((first, second) -> second)
                                                        .orElse(clientIp)
                                        )
                                        .orElse("")
                                )
                )
                .append(separator).append(loginUserId)
                .append(separator).append(ua)
                .append(separator).append(version)
                .append(separator).append(uniqueId)
                .append(separator).append(os)
                .append(separator).append(client)
                .append(separator).append(contestSeasonId);
        Log log = loggerMap.computeIfAbsent(entityName + "." + action, k -> LogFactory.getLog(LogController.class.getName() + "." + entityName + "." + action));
        logger.info("img:logger:" + LogController.class.getName() + "." + entityName + "." + action + ",info:" + log.isInfoEnabled());
        log.info(message);

        if ("item".equals(entityName) && "view".equals(action)) {
            ItemViewEvent event = new ItemViewEvent();
            event.setLoginUserId(loginUserId);
            event.setItemId(id);
            event.setTime(System.currentTimeMillis());
            applicationEventPublisher.publishEvent(event);
        } else if ("marketing".equals(entityName)) {
            try {
                MarketingActionEvent event = new MarketingActionEvent();
                event.setLoginUserId(loginUserId);
                event.setMarketingId(id);
                event.setTime(System.currentTimeMillis());
                event.setAction(MarketingActionMinute.Action.valueOf(action));
                applicationEventPublisher.publishEvent(event);
            } catch (IllegalArgumentException e) {
                logger.warn("unknown action:" + action, e);
            }
        } else if ("works".equals(entityName) && "view".equals(action)) {
            WorksViewEvent worksViewEvent = new WorksViewEvent();
            worksViewEvent.setLoginUserId(loginUserId);
            worksViewEvent.setWorksId(id);
            worksViewEvent.setTime(System.currentTimeMillis());
            applicationEventPublisher.publishEvent(worksViewEvent);
        }else if ("article".equals(entityName) && "viewtime".equals(action)) {
            ArticleViewTimeEvent articleViewTimeEvent = new ArticleViewTimeEvent();
            articleViewTimeEvent.setLoginUserId(loginUserId);
            articleViewTimeEvent.setArticleId(id);
            articleViewTimeEvent.setViewTime(viewTime);
            articleViewTimeEvent.setClient(client);
            applicationEventPublisher.publishEvent(articleViewTimeEvent);
        }
        return pixel;
    }


}

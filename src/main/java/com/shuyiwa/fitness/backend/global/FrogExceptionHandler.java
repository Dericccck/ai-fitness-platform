package com.shuyiwa.fitness.backend.global;

import com.shuyiwa.fitness.backend.Utils;
import com.shuyiwa.fitness.backend.domain.RestResponse;
import com.shuyiwa.fitness.backend.service.WarnService;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.AnonymousAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.authentication.rememberme.CookieTheftException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.context.request.ServletWebRequest;
import org.springframework.web.context.request.WebRequest;
import org.springframework.web.multipart.MaxUploadSizeExceededException;

import javax.servlet.http.HttpServletRequest;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.Optional;
import java.util.stream.Collectors;

import static com.shuyiwa.fitness.backend.global.FrogException.*;

@ControllerAdvice
public class FrogExceptionHandler {
    private static final Log logger = LogFactory.getLog(FrogExceptionHandler.class);
    @Autowired
    WarnService warnService;

    @ExceptionHandler(MissingServletRequestParameterException.class)
    public final ResponseEntity<RestResponse> handleMissingServletRequestParameterExceptionn(Throwable ex, WebRequest request) {
        logger.info("request:" + request + ",__ip:" + getIp(request) + ",xf:" + getXFor(request) + ",ex:" + ex.getMessage());
        RestResponse restResponse = new RestResponse();
        restResponse.setCode(FrogException.INTERNAL_SERVER_ERROR);
        restResponse.setMessage(ex.getMessage());
        String info = null;
        if (request instanceof ServletWebRequest) {
            HttpServletRequest httpServletRequest = ((ServletWebRequest) request).getRequest();
            info = Utils.requestInfo(httpServletRequest);
        }
        warnService.warn(request, restResponse.getMessage(), new Exception(info, ex));
        return new ResponseEntity<>(restResponse, HttpStatus.OK);
    }

    @ExceptionHandler
    public final ResponseEntity<RestResponse> handleException(Throwable ex, WebRequest request) {

        if (ex instanceof FrogException) {
            RestResponse restResponse = new RestResponse();
            restResponse.setCode(((FrogException) ex).getCode());
            restResponse.setMessage(ex.getMessage());
            logger.info(restResponse.getMessage(), ex);
            return new ResponseEntity<>(restResponse, HttpStatus.OK);
        } else if (ex instanceof AccessDeniedException) {
            if (SecurityContextHolder.getContext().getAuthentication() instanceof AnonymousAuthenticationToken) {
                //如果是未登陆，则抛出未登陆异常
                RestResponse restResponse = new RestResponse();
                restResponse.setCode(UNAUTHORIZED);
                restResponse.setMessage(ex.getMessage());
                logger.info(restResponse.getCode() + ":" + restResponse.getMessage() + ":AnonymousAuthenticationToken:request:" + request + ",__ip:" + getIp(request) + ",xf:" + getXFor(request));
                return new ResponseEntity<>(restResponse, HttpStatus.OK);
            }
            RestResponse restResponse = new RestResponse();
            restResponse.setCode(FORBIDDEN);
            restResponse.setMessage(ex.getMessage());
            logger.info(restResponse.getMessage(), ex);
            return new ResponseEntity<>(restResponse, HttpStatus.OK);
        } else if (ex instanceof CookieTheftException) {
            Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
            logger.info("CookieTheftException:" + authentication == null ? "null auth" : authentication.getClass().getName());
            logger.info("CookieTheftException:" + request.getHeader("cookie"));
            RestResponse restResponse = new RestResponse();
            restResponse.setCode(UNAUTHORIZED);
            restResponse.setMessage(ex.getMessage());
            logger.info(restResponse.getCode() + ":" + restResponse.getMessage(), ex);
            return new ResponseEntity<>(restResponse, HttpStatus.OK);
        } else if (ex instanceof MaxUploadSizeExceededException) {
            RestResponse restResponse = new RestResponse();
            restResponse.setCode(MAX_UPLOAD_SIZE_EXCEEDED_EXCEPTION);
            restResponse.setMessage(ex.getMessage());
            logger.info(restResponse.getMessage(), ex);
            return new ResponseEntity<>(restResponse, HttpStatus.OK);

        } else {
            String id = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX").format(new Date());
            id = id + Math.random();
            RestResponse restResponse = new RestResponse();
            restResponse.setCode(HttpStatus.INTERNAL_SERVER_ERROR.value());
            restResponse.setMessage("系统未知异常，异常码：" + id);
            Exception e = new FrogException(INTERNAL_SERVER_ERROR, "未知异常:" + Utils.info(request) + "", ex);
            logger.error(restResponse.getMessage(), e);
            warnService.warn(request, restResponse.getMessage(), e);
            return new ResponseEntity<>(restResponse, HttpStatus.OK);
        }
    }

    private String getIp(WebRequest request) {
        return Optional.ofNullable(request).map(req -> req.getParameter("__ip")).orElse("null");
    }

    private String getXFor(WebRequest request) {
        return Optional.ofNullable(request).map(req -> req.getHeaderValues("X-Forwarded-For")).map(xf -> Arrays.stream(xf).collect(Collectors.joining(","))).orElse("null");
    }

}

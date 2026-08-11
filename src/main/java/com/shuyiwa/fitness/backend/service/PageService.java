package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.global.FrogException;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;

@Service
public class PageService {
    private static final Log logger = LogFactory.getLog(PageService.class);

    public PageRequest getPage(int ot, int nt, int limit) throws FrogException {
        return getPage(ot, nt, limit, null);
    }

    public PageRequest getPage(int ot, int nt, int limit, Sort sort) throws FrogException {
        logger.info("ot:" + ot + ",nt:" + nt + ",limit:" + limit);
        if (ot > 0 && nt > 0) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "ot nt 不能同时指定");
        }
        int page = 0;
        if (ot > 0) {
            //上拉刷新
            page = ot;
        } else if (nt > 0) {
            //下拉刷新
            page = 0;
        } else {
            //第一次进入，等同于下拉刷新
            page = 0;
        }
        return sort == null ? PageRequest.of(page, limit) : PageRequest.of(page, limit, sort);
    }
}

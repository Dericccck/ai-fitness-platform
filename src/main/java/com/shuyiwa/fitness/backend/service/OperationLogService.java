package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.buffered.Bufferable;
import com.shuyiwa.fitness.backend.domain.OperationLogRepository;
import com.shuyiwa.fitness.backend.domain.OperationLog;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OperationLogService {
    private static final Log logger = LogFactory.getLog(OperationLogService.class);
    @Autowired
    OperationLogRepository operationLogRepository;


    @Async
    public void async(Runnable runnable) {
        runnable.run();
    }

    @Bufferable
    @Transactional(rollbackFor = Throwable.class)
    public void saveLog(OperationLog log) {
        operationLogRepository.save(log);
    }


    @Transactional
    public void clear() {
        logger.info("clear ");
        int all = operationLogRepository.clearAll(1000);
        logger.info("clear all:" + all);
    }
}

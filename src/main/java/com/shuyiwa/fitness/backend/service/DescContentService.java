package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.DescContent;
import com.shuyiwa.fitness.backend.domain.DescContentRepository;
import com.shuyiwa.fitness.backend.global.FrogException;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class DescContentService {

    private static final Log logger = LogFactory.getLog(DescContentService.class);

    @Autowired
    DescContentRepository descContentRepository;



    @Transactional(rollbackFor = Throwable.class)
    public DescContent save(DescContent descContent) throws FrogException {
        return descContentRepository.save(descContent);
    }

}

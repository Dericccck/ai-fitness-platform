package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.SmsRepository;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.domain.MessageTask;
import com.shuyiwa.fitness.backend.domain.Sms;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

import javax.annotation.PostConstruct;
import java.util.HashMap;
import java.util.Optional;

@Service
public class SmsService {
    @Autowired
    SmsRepository smsRepository;

    @Autowired(required = false)
    Sp sp;

    @PostConstruct
    void init() {
    }

    public Sms send(String phone, HashMap<String, String> params, Sms.Template template, MessageTask messageTask) throws FrogException {
        Optional<Sms> first = smsRepository.findAll(Specification
                        .where((Specification<Sms>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("phone"), phone))
                        .and((Specification<Sms>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("messageTask"), messageTask))
                , PageRequest.of(0, 1, Sort.by("createTime").descending())
        ).stream().findFirst();
        if (first.isPresent()) {
            Sms sms = new Sms();
            sms.setPhone(phone);
            smsRepository.save(sms);
            sms.setResult(Sms.SmsResult.IGNORE);
            return sms;
        }
        Sms sms = new Sms();
        sms.setPhone(phone);
        smsRepository.save(sms);
        if (sp != null) {
            sp.send(sms, params, template);
            return sms;
        }
        return sms;
    }

    public boolean send(String phone, HashMap<String, String> params, Sms.Template template) throws FrogException {
        Sms sms = new Sms();
        sms.setPhone(phone);
        smsRepository.save(sms);
        if (sp != null) {
            return sp.send(sms, params, template);
        }
        return true;
    }

    public interface Sp {
        boolean send(Sms sms, HashMap<String, String> params, Sms.Template template) throws FrogException;
    }
}

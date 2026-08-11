package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.repository.CrudRepository;

public interface NewsWechatRepository extends CrudRepository<NewsWechat, String>, JpaSpecificationExecutor<NewsWechat> {
    
}

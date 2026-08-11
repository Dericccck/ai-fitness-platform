package com.shuyiwa.fitness.backend.conf;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class CacheKeyGeneratorCon {

    @Bean
    public CacheKeyGenerator cacheKeyGenerator(){
        return new CacheKeyGeneratorImp();
    }
}

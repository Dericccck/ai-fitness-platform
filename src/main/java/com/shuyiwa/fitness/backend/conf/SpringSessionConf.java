package com.shuyiwa.fitness.backend.conf;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.core.convert.ConversionFailedException;
import org.springframework.session.Session;
import org.springframework.session.SessionRepository;
import org.springframework.session.jdbc.JdbcOperationsSessionRepository;

@Configuration
public class SpringSessionConf {
    private static final Log logger = LogFactory.getLog(SpringSessionConf.class);

    @Primary
    @Bean
    public SessionRepository primarySessionRepository(JdbcOperationsSessionRepository delegate) {
        return new InvalidClassExceptionSafeRepository(delegate);
    }

    public class InvalidClassExceptionSafeRepository<S extends Session> implements SessionRepository<S> {
        private final SessionRepository<S> repository;

        public InvalidClassExceptionSafeRepository(SessionRepository<S> repository) {
            this.repository = repository;
        }


        @Override
        public S createSession() {
            return repository.createSession();
        }

        @Override
        public void save(S s) {
            repository.save(s);
        }

        @Override
        public S findById(String s) {
            try {
                return repository.findById(s);
            } catch (ConversionFailedException e) {
                logger.warn("session restore failed", e);
                deleteById(s);
                return null;
            }
        }

        @Override
        public void deleteById(String s) {
            repository.deleteById(s);
        }
    }
}

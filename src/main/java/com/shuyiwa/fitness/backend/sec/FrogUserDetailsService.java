package com.shuyiwa.fitness.backend.sec;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.service.LoginUserService;
import com.shuyiwa.fitness.backend.util.Md5Util;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import javax.annotation.PostConstruct;
import javax.transaction.Transactional;
import java.util.*;
import java.util.stream.Collectors;

@Service
public class FrogUserDetailsService implements UserDetailsService {
    private static final Log logger = LogFactory.getLog(FrogUserDetailsService.class);
    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;
    @Autowired
    PhoneVerifyCodeRepository phoneVerifyCodeRepository;
    @Value("${com.shuyiwa.fitness.backend.phone-verify-code.expired-seconds:300}")
    Long expiredSeconds;
    @Value("${com.shuyiwa.fitness.backend.login-user.embedded:#{null}}")
    String embedded;
    @Autowired
    LoginUserService loginUserService;
    @Autowired
    StringRedisTemplate stringRedisTemplate;
    @Autowired
    private LoginUserRepository loginUserRepository;
    @Autowired
    private LoginUserRoleRepository loginUserRoleRepository;
    private Map<String, String> embeddedMap = new HashMap<>();

    @PostConstruct
    void init() {
        if (embedded != null) {
            Arrays.stream(embedded.split(",")).forEach(pair -> {
                String[] split = pair.split("/", 2);
                if (split.length == 2) {
                    embeddedMap.put(split[0], split[1]);
                }
            });
        }
    }

    @Override
    @Transactional
    public UserDetails loadUserByUsername(String userName) throws UsernameNotFoundException {
        Optional<LoginUser> loginUserOptional = loginUserRepository.findById(userName);
        if (!loginUserOptional.isPresent()) {
            logger.info("LoginUser not found! " + userName);
            throw new UsernameNotFoundException("LoginUser " + userName + " was not found in the database");
        }
        LoginUser loginUser = loginUserOptional.get();
        logger.info("Found LoginUser: " + loginUser);
        return wrapUserAuthority(loginUser);
    }

    public UserDetails findOrCreateUser(FrogAuthenticationToken authentication) {
        if("fitness_account".equals(authentication.getChannel())){
            if(StringUtils.isEmpty(authentication.getPhone()) || StringUtils.isEmpty(authentication.getCode())){
                return null;
            }
            Optional<LoginUser> loginUserOptional = loginUserRepository.findByPhoneAndPassword(authentication.getPhone(), Md5Util.string2MD5(authentication.getCode()));
            if (loginUserOptional.isPresent()) {
                LoginUser  loginUser = loginUserOptional.get();
                return wrapUserAuthority(loginUser);
            } else {
                return  null;
            }
        }else if("fitness_phonever".equals(authentication.getChannel())){
            String embeddedCode = embeddedMap.get(authentication.getPhone());
            String embeddedCodeInRedis = null;
            try {
                embeddedCodeInRedis = stringRedisTemplate.opsForValue().get("code_" + authentication.getPhone());
            } catch (Throwable e) {
                logger.warn("cannot get value from redis", e);
            }

            String pythonVerifyCode = null;
            Optional<PhoneVerifyCode> verifyCodeOptional = phoneVerifyCodeRepository.findPhoneVerifyCode(authentication.getPhone(), expiredSeconds);
            if (verifyCodeOptional.isPresent()) {
                pythonVerifyCode = verifyCodeOptional.get().getCode();
                logger.info("pythonVerifyCode:" + verifyCodeOptional.isPresent());
            }
            if (authentication.getCode() != null) {
                if (notEmptyAndEquals(embeddedCode, authentication.getCode()) || notEmptyAndEquals(embeddedCodeInRedis, authentication.getCode()) || authentication.getCode().equals(pythonVerifyCode)) {
                    Optional<LoginUser> loginUserOptional = loginUserRepository.findByPhone(authentication.getPhone());
                    LoginUser loginUser;
                    if (!loginUserOptional.isPresent()) {
                        loginUser = loginUserService.createLoginUser(authentication.getPhone(),null);
                    } else {
                        loginUser = loginUserOptional.get();
                    }
    //                loginUser.setProperty("availableVotes", 100);
                    return wrapUserAuthority(loginUser);
                } else {
                    return null;
                }
            } else {
                return null;
            }
        }else{
            return null;
        }
    }

    private boolean notEmptyAndEquals(String code, String codeParameter) {
        return !StringUtils.isEmpty(code) && code.equals(codeParameter);
    }

    private UserDetails wrapUserAuthority(LoginUser loginUser) {
        List<GrantedAuthority> authorityList = getGrantedAuthorities(loginUser.getId());
        FrogUserDetails frogUserDetails = new FrogUserDetails(loginUser, authorityList);
        frogUserDetails.setProperty("loginUser", loginUser);
        return frogUserDetails;
    }

    public List<GrantedAuthority> getGrantedAuthorities(String loginUserId) {
        List<GrantedAuthority> authorityList = new ArrayList<>();
        authorityList.addAll(loginUserAuthorityRepository.findByLoginUser_IdOrderByAuthorityAsc(loginUserId)
                .stream()
                .flatMap(a -> {
                    List<GrantedAuthorityWithEntity> list = new ArrayList<>();
                    list.add(new GrantedAuthorityWithEntity(a.getAuthority(), a.getEntityId()));
                    a.getAuthority().getChildren().forEach(child -> {
                        list.add(new GrantedAuthorityWithEntity(child, a.getEntityId()));
                    });
                    return list.stream();
                })
                .collect(Collectors.toList()));
        return authorityList;
    }

    public static class GrantedAuthorityWithEntity implements GrantedAuthority {
        private final Authority authorityEnum;
        private final String entityId;

        public GrantedAuthorityWithEntity(Authority authority, String entityId) {
            this.authorityEnum = authority;
            this.entityId = entityId;
        }


        @Override
        public String getAuthority() {
            return authorityEnum.name();
        }

        @Override
        public boolean equals(Object o) {
            if (this == o) return true;
            if (o == null || getClass() != o.getClass()) return false;
            GrantedAuthorityWithEntity that = (GrantedAuthorityWithEntity) o;
            return authorityEnum == that.authorityEnum &&
                    Objects.equals(entityId, that.entityId);
        }

        @Override
        public int hashCode() {
            return Objects.hash(authorityEnum, entityId);
        }

        @JsonIgnore
        public Authority getAuthorityEnum() {
            return authorityEnum;
        }

        public String getEntityId() {
            return entityId;
        }
    }
}
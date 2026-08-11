package com.shuyiwa.fitness.backend.sec;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.shuyiwa.fitness.backend.domain.LoginUser;
import com.shuyiwa.fitness.backend.domain.LoginUserRepository;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.util.StringUtils;

import javax.persistence.Transient;
import java.util.*;

public class FrogUserDetails implements UserDetails {
    private static final Log logger = LogFactory.getLog(FrogUserDetails.class);
    private final String loginUserId;
    private List<GrantedAuthority> authorityList;
    private final String password;
    private final String phone;
    @Transient
    private Map<String, Object> properties = new HashMap<>();

    public FrogUserDetails(LoginUser loginUser, List<GrantedAuthority> authorityList) {
        this.loginUserId = loginUser.getId();
        this.password = loginUser.getPassword();
        this.phone = loginUser.getPhone();
        if (StringUtils.isEmpty(loginUserId)) {
            logger.warn("loginUserId is null", new Exception());
        }
        this.authorityList = authorityList;
    }

    public void setAuthorityList(List<GrantedAuthority> authorityList) {
        this.authorityList = authorityList;
    }

    @Override
    public Collection<? extends GrantedAuthority> getAuthorities() {
        return authorityList;
    }

    @Override
    public String getPassword() {
        return password;
    }

    @Override
    public boolean isAccountNonExpired() {
        return true;
    }

    @Override
    public boolean isAccountNonLocked() {
        return true;
    }

    @Override
    public boolean isCredentialsNonExpired() {
        return true;
    }

    @Override
    public boolean isEnabled() {
        return true;
    }

    @Override
    public String getUsername() {
        return loginUserId == null ? "" : loginUserId;
    }
    //TODO：api还要取？
//    public LoginUser getLoginUser() {
//        return loginUser;
//    }

    public LoginUser getLoginUser(LoginUserRepository loginUserRepository) {
        return Optional.ofNullable(loginUserId).map(id -> loginUserRepository.findById(id).orElse(null)).orElse(null);
    }

    @JsonAnyGetter
    public Map<String, Object> getProperties() {
        return properties;
    }

    @JsonAnySetter
    public void setProperty(String name, Object value) {
        properties.put(name, value);
    }

    public String getLoginUserId() {
        return loginUserId;
    }

    public String getPhone() {
        return phone;
    }
}

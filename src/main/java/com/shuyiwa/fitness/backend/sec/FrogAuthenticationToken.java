package com.shuyiwa.fitness.backend.sec;

import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.GrantedAuthority;

import java.util.Collection;

public class FrogAuthenticationToken extends UsernamePasswordAuthenticationToken {
    private final String channel;
    private final String phone;
    private final String code;

    public FrogAuthenticationToken(String phone, String code, String channel) {
        super(phone, code);
        this.channel = channel;
        this.phone = phone;
        this.code = code;
    }

    public FrogAuthenticationToken(String phone, String code, String channel, Collection<? extends GrantedAuthority> authorities) {
        super(phone, code, authorities);
        this.channel = channel;
        this.phone = phone;
        this.code = code;
    }

    public String getChannel() {
        return channel;
    }

    public String getPhone() {
        return phone;
    }

    public String getCode() {
        return code;
    }
}

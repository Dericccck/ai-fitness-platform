package com.shuyiwa.fitness.backend.sec;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.authentication.InternalAuthenticationServiceException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.authentication.dao.AbstractUserDetailsAuthenticationProvider;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.stereotype.Component;

@Component
public class FrogUserDetailsAuthenticationProvider extends AbstractUserDetailsAuthenticationProvider {
    @Autowired
    private FrogUserDetailsService frogUserDetailsService;

    @Override
    protected void additionalAuthenticationChecks(UserDetails userDetails, UsernamePasswordAuthenticationToken authentication) throws AuthenticationException {
        if (userDetails instanceof FrogUserDetails && authentication instanceof FrogAuthenticationToken) {
            additionalAuthenticationChecks((FrogUserDetails) userDetails, (FrogAuthenticationToken) authentication);
        } else {
            logger.info("Authentication failed: authentication method not support");
            throw new BadCredentialsException(
                    messages.getMessage("AbstractUserDetailsAuthenticationProvider.badCredentials", "Bad credentials"));
        }
    }

    @Override
    protected UserDetails retrieveUser(String username, UsernamePasswordAuthenticationToken authentication) throws AuthenticationException {
        if (authentication instanceof FrogAuthenticationToken) {
            return retrieveUser(username, (FrogAuthenticationToken) authentication);
        }
        throw new InternalAuthenticationServiceException("authentication not supported");
    }

    private void additionalAuthenticationChecks(FrogUserDetails userDetails, FrogAuthenticationToken authentication) throws AuthenticationException {
        if (authentication.getCredentials() == null) {
            logger.debug("Authentication failed: no credentials provided");
            throw new BadCredentialsException(
                    messages.getMessage("AbstractUserDetailsAuthenticationProvider.badCredentials", "Bad credentials"));
        }


    }

    private UserDetails retrieveUser(String username, FrogAuthenticationToken authentication) throws AuthenticationException {
        UserDetails user = frogUserDetailsService.findOrCreateUser(authentication);
        if (user == null) {
            throw new UsernameNotFoundException("frog user:" + username + " not found");
        }
        return user;
    }

}

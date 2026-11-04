package com.shuyiwa.fitness.backend.sec;

import com.shuyiwa.fitness.backend.domain.LoginUser;
import org.junit.Test;
import org.springframework.security.authentication.DisabledException;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Collections;

import static org.junit.Assert.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

public class FrogUserDetailsEnabledTest {
    private FrogUserDetails details(Boolean enabled) {
        LoginUser user = new LoginUser();
        user.setId("test-user");
        user.setEnabled(enabled);
        return new FrogUserDetails(user, Collections.emptyList());
    }

    @Test public void onlyExplicitlyEnabledAccountsAreEnabled() {
        assertTrue(details(true).isEnabled());
        assertFalse(details(false).isEnabled());
        assertFalse(details(null).isEnabled());
    }

    @Test public void securityProviderRejectsDisabledAccounts() {
        FrogUserDetailsService service = mock(FrogUserDetailsService.class);
        FrogUserDetailsAuthenticationProvider provider = new FrogUserDetailsAuthenticationProvider();
        ReflectionTestUtils.setField(provider, "frogUserDetailsService", service);
        when(service.findOrCreateUser(any(FrogAuthenticationToken.class))).thenReturn(details(false));
        try {
            provider.authenticate(new FrogAuthenticationToken("test-phone", "test-code", "fitness_account"));
            fail("disabled account was authenticated");
        } catch (DisabledException expected) {
            // The database status is checked by Spring's pre-authentication checks.
        }
        when(service.findOrCreateUser(any(FrogAuthenticationToken.class))).thenReturn(details(true));
        assertTrue(provider.authenticate(new FrogAuthenticationToken("test-phone", "test-code", "fitness_account")).isAuthenticated());
    }
}

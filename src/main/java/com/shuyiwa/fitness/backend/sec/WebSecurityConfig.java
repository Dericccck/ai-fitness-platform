package com.shuyiwa.fitness.backend.sec;


import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.backend.domain.RestResponse;
import com.shuyiwa.fitness.backend.service.LoginUserService;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.authentication.builders.AuthenticationManagerBuilder;
import org.springframework.security.config.annotation.method.configuration.EnableGlobalMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configuration.WebSecurityConfigurerAdapter;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.web.authentication.RememberMeServices;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.security.web.authentication.rememberme.*;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.sql.DataSource;
import java.time.Duration;
import java.util.Arrays;
import java.util.Date;
import java.util.Optional;

import static com.shuyiwa.fitness.backend.global.FrogException.OK;
import static com.shuyiwa.fitness.backend.global.FrogException.UNAUTHORIZED;

@Configuration
@EnableWebSecurity
@EnableGlobalMethodSecurity(securedEnabled = true, prePostEnabled = true, jsr250Enabled = true)
public class WebSecurityConfig extends WebSecurityConfigurerAdapter {
    private static final Log logger = LogFactory.getLog(WebSecurityConfig.class);
    @Autowired
    FrogUserDetailsAuthenticationProvider frogUserDetailsAuthenticationProvider;
    @Autowired
    ObjectMapper mapper;
    @Autowired
    LoginUserService loginUserService;
    @Autowired
    private FrogUserDetailsService userDetailsService;
    @Autowired
    private DataSource dataSource;
    @Autowired
    private RestAuthenticationEntryPoint restAuthenticationEntryPoint;
    @Autowired
    private ApplicationEventPublisher applicationEventPublisher;

    @Bean
    public RefreshFilter refreshFilger() {
        return new RefreshFilter();
    }

    @Bean
    public BCryptPasswordEncoder passwordEncoder() {
        BCryptPasswordEncoder bCryptPasswordEncoder = new BCryptPasswordEncoder();
        return bCryptPasswordEncoder;
    }

    @Autowired
    public void configureGlobal(AuthenticationManagerBuilder auth) throws Exception {
        auth.authenticationProvider(frogUserDetailsAuthenticationProvider);
    }

    @Bean
    public FrogAuthenticationFilter authenticationFilter() throws Exception {
        FrogAuthenticationFilter authenticationFilter
                = new FrogAuthenticationFilter();
        authenticationFilter.setAuthenticationManager(authenticationManagerBean());
        authenticationFilter.setRememberMeServices(rememberMeServices());
        authenticationFilter.setAuthenticationSuccessHandler((request, response, authentication) -> {
            response.setHeader("Content-Type", "application/json;charset=UTF-8");
            RestResponse restResponse = new RestResponse();
            restResponse.setCode(OK);
            restResponse.setData(authentication.getPrincipal());
            String message = mapper.writeValueAsString(restResponse);
            response.getWriter().write(message);
            loginUserService.sendLoinUserActivityEvent(request, Optional.ofNullable(authentication.getPrincipal())
                    .filter(f -> f instanceof FrogUserDetails)
                    .map(f -> (FrogUserDetails) f)
                    .orElse(null)
            );
        });
        authenticationFilter.setAuthenticationFailureHandler((request, response, exception) -> {
            response.setHeader("Content-Type", "application/json;charset=UTF-8");
            RestResponse restResponse = new RestResponse();
            restResponse.setCode(UNAUTHORIZED);
            restResponse.setMessage("验证码或密码校验失败");
            String message = mapper.writeValueAsString(restResponse);
            response.getWriter().write(message);
            logger.info("authenticationFilter.setAuthenticationFailureHandler:" + UNAUTHORIZED + ",path:" + request.getServletPath() + "," + message, exception);
        });
        return authenticationFilter;
    }

    @Bean
    public RememberMeServices rememberMeServices() {
        PersistentTokenRepository tokenRepository = persistentTokenRepository();
        PersistentTokenBasedRememberMeServices rememberMeServices = new PersistentTokenBasedRememberMeServices("frog", userDetailsService, tokenRepository) {
            @Override
            protected UserDetails processAutoLoginCookie(String[] cookieTokens, HttpServletRequest request, HttpServletResponse response) {
                if (cookieTokens.length != 2) {
                    throw new InvalidCookieException("Cookie token did not contain " + 2
                            + " tokens, but contained '" + Arrays.asList(cookieTokens) + "'");
                }

                final String presentedSeries = cookieTokens[0];
                final String presentedToken = cookieTokens[1];

                PersistentRememberMeToken token = tokenRepository
                        .getTokenForSeries(presentedSeries);

                if (token == null) {
                    // No series match, so we can't authenticate using this cookie
                    throw new RememberMeAuthenticationException(
                            "No persistent token found for series id: " + presentedSeries);
                }

                // We have a match for this user/series combination
                boolean tokenRecentUpdate = token.getDate().before(new Date(new Date().getTime() - Duration.ofMinutes(15).toMillis()));
                if (!presentedToken.equals(token.getTokenValue()) && tokenRecentUpdate) {
                    // Token doesn't match series value. Delete all logins for this user and throw
                    // an exception to warn them.
                    tokenRepository.removeUserTokens(token.getUsername());

                    throw new CookieTheftException(
                            messages.getMessage(
                                    "PersistentTokenBasedRememberMeServices.cookieStolen",
                                    "Invalid remember-me token (Series/token) mismatch. Implies previous cookie theft attack."));
                }

                if (token.getDate().getTime() + getTokenValiditySeconds() * 1000L < System
                        .currentTimeMillis()) {
                    throw new RememberMeAuthenticationException("Remember-me login has expired");
                }

                // Token also matches, so login is valid. Update the token value, keeping the
                // *same* series number.
                if (logger.isDebugEnabled()) {
                    logger.debug("Refreshing persistent login token for user '"
                            + token.getUsername() + "', series '" + token.getSeries() + "'");
                }
                if (!tokenRecentUpdate) {//如果token刚更新，则令原token有效，并且不下发新token
                    PersistentRememberMeToken newToken = new PersistentRememberMeToken(
                            token.getUsername(), token.getSeries(), generateTokenData(), new Date());

                    try {
                        tokenRepository.updateToken(newToken.getSeries(), newToken.getTokenValue(),
                                newToken.getDate());
                        addCookie(newToken, request, response);
                    } catch (Exception e) {
                        logger.error("Failed to update token: ", e);
                        throw new RememberMeAuthenticationException(
                                "Autologin failed due to data access problem");
                    }
                }
                return getUserDetailsService().loadUserByUsername(token.getUsername());
            }

            private void addCookie(PersistentRememberMeToken token, HttpServletRequest request,
                                   HttpServletResponse response) {
                setCookie(new String[]{token.getSeries(), token.getTokenValue()},
                        getTokenValiditySeconds(), request, response);
            }
        };
        rememberMeServices.setTokenValiditySeconds(1 * 24 * 60 * 60 * 365);//1 year
        return rememberMeServices;
    }


    @Override
    protected void configure(HttpSecurity http) throws Exception {
        http
                .addFilterBefore(authenticationFilter(), UsernamePasswordAuthenticationFilter.class)
                .csrf().disable()
                .exceptionHandling()
                .accessDeniedHandler((request, response, accessDeniedException) -> {
                    response.setHeader("Content-Type", "application/json;charset=UTF-8");
                    response.getWriter().write("{\"code\":" + UNAUTHORIZED + ",\"message\":\"认证失败\",\"data\":false}");
                    logger.info(".accessDeniedHandler:" + UNAUTHORIZED + ",path:" + request.getServletPath(), accessDeniedException);
                })
                .authenticationEntryPoint(restAuthenticationEntryPoint)
                .and()
                .authorizeRequests()
//                .antMatchers("/api/sec/sendVerifyCode","/login","/logout").permitAll()
                .antMatchers("/api/**").permitAll()
                .and()
                .formLogin()
                .and()
                .logout()
                .logoutSuccessHandler((request, response, authentication) -> {
                    response.setHeader("Content-Type", "application/json;charset=UTF-8");
                    response.getWriter().write("{\"code\":" + OK + ",\"message\":\"\",\"data\":true}");
                    logger.info(".addLogoutHandler:" + UNAUTHORIZED);
                });
////        // Config Remember Me.
        http.authorizeRequests().and() //
                .rememberMe().tokenRepository(this.persistentTokenRepository()) //
                .key("fitness")
                .rememberMeServices(rememberMeServices())
                .userDetailsService(userDetailsService)
                .tokenValiditySeconds(1 * 24 * 60 * 60 * 365); // 1 year
        http.apply(refreshFilger());

    }


    @Bean
    public PersistentTokenRepository persistentTokenRepository() {
        JdbcTokenRepositoryImpl db = new JdbcTokenRepositoryImpl() {
            @Override
            protected void initDao() {
                getJdbcTemplate().execute(CREATE_TABLE_SQL.replace("create table", "CREATE TABLE IF NOT EXISTS"));
            }
        };
        db.setDataSource(dataSource);
        return db;
    }


}
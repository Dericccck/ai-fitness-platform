package com.shuyiwa.fitness.backend.sec;

import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.service.AccountService;
import com.shuyiwa.fitness.backend.service.LoginUserFileService;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.service.SmsService;
import org.apache.commons.lang.ObjectUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.BeanWrapper;
import org.springframework.beans.BeanWrapperImpl;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.Cursor;
import org.springframework.data.redis.core.RedisCallback;
import org.springframework.data.redis.core.ScanOptions;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.session.jdbc.JdbcOperationsSessionRepository;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.RequestParam;

import javax.imageio.ImageIO;
import java.awt.*;
import java.awt.image.BufferedImage;
import java.beans.FeatureDescriptor;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.math.BigDecimal;
import java.util.*;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.stream.Stream;

import static com.shuyiwa.fitness.backend.domain.Sms.Template.VerifyCode;

@Service
public class SecService {
    private static final Log logger = LogFactory.getLog(SecService.class);
    @Autowired
    ImgVerifyCodeRepository imgVerifyCodeRepository;
    @Autowired
    StringRedisTemplate stringRedisTemplate;
    @Autowired
    PhoneVerifyCodeRepository phoneVerifyCodeRepository;
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    LoginUserFileService loginUserFileService;
    @Autowired
    SmsService smsService;
    @Autowired
    ContestantInfoRepository contestantInfoRepository;
    @Autowired
    JdbcOperationsSessionRepository jdbcOperationsSessionRepository;

    public static String[] getNullPropertyNames(Object source) {
        final BeanWrapper wrappedSource = new BeanWrapperImpl(source);
        return Stream.of(wrappedSource.getPropertyDescriptors())
                .map(FeatureDescriptor::getName)
                .filter(propertyName -> wrappedSource.getPropertyValue(propertyName) == null)
                .toArray(String[]::new);
    }

    public void sendVerifyCode(String clientIp, String phone) throws FrogException {
        long ipSeconds = 600;
        long phoneSeconds = 600;
        String ipKey = "S" + ipSeconds + "::sendVerifyCode:ip:" + clientIp;
        int ipMax = 100;
        String phoneKey = "S" + phoneSeconds + "::sendVerifyCode:phone:" + phone;
        int phoneMax = 3;
        checkFrequency(ipKey, ipMax, "当前IP短信验证过于频繁，请稍后再试");
        checkFrequency(phoneKey, phoneMax, "当前手机短信验证过于频繁，请稍后再试");


        Random random = new Random();
        StringBuilder codeBuilder = new StringBuilder();
        for (int i = 0; i < 5; i++) {
            codeBuilder.append(random.nextInt(10));
        }
        String code = codeBuilder.toString();

        if (!smsService.send(phone, new HashMap<String, String>() {{
            put("code", code);
        }}, VerifyCode)) {
            throw new FrogException(FrogException.PHONE_VERIFY_CODE_SEND_FAILED, "短信发送失败");
        }


        PhoneVerifyCode phoneVerifyCode = new PhoneVerifyCode();
        phoneVerifyCode.setCode(code);
        phoneVerifyCode.setDeleted(false);
        phoneVerifyCode.setIp(clientIp);
        phoneVerifyCode.setPhone(phone);
        phoneVerifyCodeRepository.save(phoneVerifyCode);

        stringRedisTemplate.opsForValue().set(ipKey, "", ipSeconds, TimeUnit.SECONDS);
        stringRedisTemplate.opsForValue().set(ipKey + ":" + phone + ":" + code, "", ipSeconds, TimeUnit.SECONDS);

        stringRedisTemplate.opsForValue().set(phoneKey, "", phoneSeconds, TimeUnit.SECONDS);
        stringRedisTemplate.opsForValue().set(phoneKey + ":" + clientIp + ":" + code, "", phoneSeconds, TimeUnit.SECONDS);
    }

    private void checkFrequency(String prefix, int max, String message) throws FrogException {
        if (stringRedisTemplate.hasKey(prefix)) {
            Integer count = stringRedisTemplate.execute((RedisCallback<Integer>) connection -> {
                try (Cursor<byte[]> cursor = connection.scan(
                        ScanOptions.scanOptions().match(prefix + "*").build())) {
                    int count1 = 0;
                    while (cursor.hasNext()) {
                        count1++;
                        cursor.next();
                        if (count1 > max) {
                            return count1;
                        }
                    }
                    return count1;
                } catch (IOException e) {
                    e.printStackTrace();
                    return -1;
                }
            });
            if (count > max) {
                throw new FrogException(FrogException.PHONE_VERIFY_CODE_SEND_TOO_MANY_TIMES, message, "prefix:" + prefix + ",gt than:" + max);
            }
        }
    }

    public ImgVerifyCode newImgVerifyCode() throws IOException {
        Random random = new Random();
        int authCodeLength = 5; // length of verification code
        int singleCodeWidth = 10; // width of one digit or character in the image
        int singleCodeHeight = 25; // height of one digit or character in the image
        int singleCodeGap = 5; // margin of a digit or character
        int paddingTopBot = 10;// padding of top and bottom
        int paddingLeftRight = 10; //padding of left and right
        int imgWidth = authCodeLength * (singleCodeWidth + singleCodeGap) + paddingLeftRight;
        int imgHeight = singleCodeHeight + paddingTopBot;
        char[] CHARS = {'0', '1', '2', '3', '4', '5', '6', '7', '8',
                '9', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
                'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
                'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M',
                'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z'};


        BufferedImage img = new BufferedImage(imgWidth, imgHeight,
                BufferedImage.TYPE_INT_BGR);

        Graphics g = img.getGraphics();
        // Set the img background
        g.setColor(new Color(255, 255, 230));
        // draw a rectangle
        g.fillRect(0, 0, imgWidth, imgHeight);
        // color of verification code
        g.setColor(Color.BLACK);
        // Set font details
        g.setFont(new Font("Arial", Font.PLAIN, singleCodeHeight + 5));
        //draw the code in the image
        StringBuilder code = new StringBuilder();
        for (int i = 0; i < 5; i++) {
            char c = CHARS[random.nextInt(CHARS.length)];
            code.append(c);
            g.drawString(c + "", i * (singleCodeWidth + singleCodeGap)
                    + singleCodeGap / 2 + paddingLeftRight / 2, imgHeight - paddingTopBot / 2);
        }
        // add elements - draw some random lines
        for (int i = 0; i < 10; i++) {
            int x = random.nextInt(imgWidth);
            int y = random.nextInt(imgHeight);
            int x2 = random.nextInt(imgWidth);
            int y2 = random.nextInt(imgHeight);
            g.drawLine(x, y, x + x2, y + y2);
        }

        ImgVerifyCode imgVerifyCode = new ImgVerifyCode();
        imgVerifyCode.setCode(code.toString());
        try (ByteArrayOutputStream os = new ByteArrayOutputStream()) {
            ImageIO.write(img, "png", os);
            String s = Base64.getEncoder().encodeToString(os.toByteArray());
            imgVerifyCode.setImg(s);
        }
        imgVerifyCodeRepository.save(imgVerifyCode);
        return imgVerifyCode;
    }

    public void imgVerifyCodeCheck(String imgVerifyCodeId, String imgVerifyCodeCode) throws FrogException {
        boolean verified = false;
        Optional<ImgVerifyCode> imgVerifyCode = imgVerifyCodeRepository.findById(imgVerifyCodeId);
        if (imgVerifyCode.isPresent()) {
            String code = imgVerifyCode.get().getCode();
            if (code != null && code.equalsIgnoreCase(imgVerifyCodeCode)) {
                verified = true;
            }
        }
        if (!verified) {
            throw new FrogException(FrogException.IMG_VERIFY_CODE_NOT_MATCH, "image verify code not match");
        }
    }

    //2020-07-02 1.3.2开始不使用这个接口
    @Deprecated
    public LoginUser saveOld(String id, String avatarFileId, LoginUser loginUser) throws FrogException {
        logger.info("save login user:avatarFileId:" + avatarFileId);
        Optional<LoginUser> loginUserOptional = loginUserRepository.findById(id);
        if (loginUserOptional.isPresent()) {
            LoginUser loginUserInDb = loginUserOptional.get();
            if (loginUser != null) {
                boolean wantChangeEditTimesFields = Arrays.stream(LoginUser.class.getDeclaredFields()).filter(field -> field.isAnnotationPresent(LoginUser.EditTimes.class))
                        .anyMatch(field -> {
                            field.setAccessible(true);
                            try {
                                Object newValue = field.get(loginUser);
                                boolean equals = ObjectUtils.equals(newValue, field.get(loginUserInDb));
                                logger.info("change user filed:" + field.getName() + ",old:" + field.get(loginUserInDb) + ",new:" + newValue + ",equals:" + equals);
                                return !equals && newValue != null;
                            } catch (IllegalAccessException e) {
                                return false;
                            }
                        });
                logger.info("wantChangeEditTimesFields:" + wantChangeEditTimesFields);
                if (wantChangeEditTimesFields && !loginUserInDb.isEditable()) {
                    throw new FrogException(FrogException.FORBIDDEN, "用户已无权限修改用户信息");
                }
                if (wantChangeEditTimesFields) {
                    loginUserInDb.setEditTimes(loginUserInDb.getEditTimes() + 1);
                }
                if (loginUser.getName() != null) {
                    List<LoginUser> names = loginUserRepository.findByName(loginUser.getName());
                    for (LoginUser name : names) {
                        if (!name.getId().equals(loginUserInDb.getId())) {
                            throw new FrogException(FrogException.FORBIDDEN, "该用户名已经有人使用，请换用其他用户再试，谢谢");
                        }
                    }
                    loginUserInDb.setName(loginUser.getName());
                }
                if (loginUser.getSex() != null) {
                    loginUserInDb.setSex(loginUser.getSex());
                }
                loginUserRepository.save(loginUserInDb);
            }
            logger.info("save login user1:avatarFileId:" + avatarFileId);
            if (avatarFileId != null) {
                logger.info("save login user2:avatarFileId:" + avatarFileId);
                loginUserFileService.use(avatarFileId, loginUserInDb);
            }
            return loginUserInDb;
        }
        return null;
    }

    @Autowired
    AccountService accountService;


    //自己修改自己的用户信息
    public LoginUser modifyUserBySelf(String id, String avatarFileId, LoginUser loginUser) throws FrogException {
        logger.info("modifyUserBySelf:avatarFileId:" + avatarFileId);
        Optional<LoginUser> loginUserOptional = loginUserRepository.findById(id);
        if (loginUserOptional.isPresent()) {
            LoginUser loginUserInDb = loginUserOptional.get();
            boolean isEdit = false;
            if (loginUser != null) {
                if (loginUser.getName() != null) {
                    /*List<LoginUser> names = loginUserRepository.findByName(loginUser.getName());
                    for (LoginUser name : names) {
                        if (!name.getId().equals(loginUserInDb.getId())) {
                            throw new FrogException(FrogException.FORBIDDEN, "该用户名已经有人使用，请换用其他用户再试，谢谢");
                        }
                    }*/

                    if (!loginUser.getName().equals(loginUserInDb.getName())) {
                        /*if(loginUserInDb.getEditTimes()>=loginUserInDb.getMaxEditTimes()){
                            accountService.subtract("积分交易", loginUserInDb, new BigDecimal(500), "修改昵称:" + loginUserInDb.getName() + "->" + loginUser.getName());
                        }*/
                        loginUserInDb.setName(loginUser.getName());
                        isEdit=true;
                    }
                }
                if(!StringUtils.isEmpty(loginUser.getIdCard()) && !loginUser.getIdCard().equals(loginUserInDb.getIdCard())){
                    loginUserInDb.setIdCard(loginUser.getIdCard());
                    isEdit=true;
                }
                if(loginUser.getBirthDay()!=null && loginUser.getBirthDay()!=loginUserInDb.getBirthDay()){
                    loginUserInDb.setBirthDay(loginUser.getBirthDay());
                    isEdit=true;
                }
                if (loginUser.getSex() != null ) {
                    if(loginUserInDb.getSex()==null  || (loginUserInDb.getSex()!=null  && !loginUser.getSex().name().equals(loginUserInDb.getSex().name()))) {
                        loginUserInDb.setSex(loginUser.getSex());
                        isEdit = true;
                    }
                }
                if(isEdit){
                    loginUserInDb.setEditTimes(loginUserInDb.getEditTimes()+1);
                    loginUserRepository.save(loginUserInDb);
                }
            }
            logger.info("save login user1:avatarFileId:" + avatarFileId);
            if (avatarFileId != null) {
                logger.info("save login user2:avatarFileId:" + avatarFileId);
                loginUserFileService.use(avatarFileId, loginUserInDb);
            }
            return loginUserInDb;
        }
        return null;
    }

    public Boolean checkVerifyCode (String phone,String code) throws FrogException{
        Optional<PhoneVerifyCode> verifyCodeOptional = phoneVerifyCodeRepository.findPhoneVerifyCode(phone,300);
        if (verifyCodeOptional.isPresent()) {
            if(code.equals(verifyCodeOptional.get().getCode())){
                return true;
            }else {
                return false;
            }
        }else {
            return false;
        }
    }

    public void forceLogout( String userId) {
        Map userSessions = jdbcOperationsSessionRepository.findByPrincipalName(userId);
        if (userSessions != null && !userSessions.isEmpty()) {
            List sessionIds = new ArrayList<>(userSessions.keySet());
            for (Object sessionId : sessionIds) {
                jdbcOperationsSessionRepository.deleteById(sessionId.toString());
            }

        }

    }

}

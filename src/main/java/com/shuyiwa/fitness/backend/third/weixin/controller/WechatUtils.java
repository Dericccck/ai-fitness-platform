package com.shuyiwa.fitness.backend.third.weixin.controller;

import org.bouncycastle.jce.provider.BouncyCastleProvider;

import javax.crypto.BadPaddingException;
import javax.crypto.Cipher;
import javax.crypto.IllegalBlockSizeException;
import javax.crypto.NoSuchPaddingException;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.io.UnsupportedEncodingException;
import java.nio.charset.StandardCharsets;
import java.security.AlgorithmParameters;
import java.security.InvalidAlgorithmParameterException;
import java.security.InvalidKeyException;
import java.security.Key;
import java.security.NoSuchAlgorithmException;
import java.security.NoSuchProviderException;
import java.security.Security;
import java.security.spec.InvalidParameterSpecException;
import java.util.Base64;

public class WechatUtils {
    static {
        Security.addProvider(new BouncyCastleProvider());
    }

    /**
     * 解密
     *
     * @param sessionKey
     * @param iv
     * @param encryptedData
     * @return
     * @throws NoSuchPaddingException
     * @throws NoSuchAlgorithmException
     * @throws InvalidParameterSpecException
     * @throws InvalidAlgorithmParameterException
     * @throws InvalidKeyException
     * @throws BadPaddingException
     * @throws IllegalBlockSizeException
     */
    public static String decryptWeChatData(String sessionKey, String iv, String encryptedData) {
        final Base64.Decoder decoder = Base64.getMimeDecoder();
        byte[] sessionKeyByte = decoder.decode(sessionKey);
        byte[] encryptedDataByte = decoder.decode(encryptedData);
        byte[] ivByte = decoder.decode(iv);

        byte[] bytes;
        try {
            Key key = new SecretKeySpec(sessionKeyByte, "AES");
            AlgorithmParameters algorithmParameters = null;
            Cipher cipher = null;
            algorithmParameters = AlgorithmParameters.getInstance("AES");
            algorithmParameters.init(new IvParameterSpec(ivByte));
            cipher = Cipher.getInstance("AES/CBC/PKCS7Padding");
            cipher.init(Cipher.DECRYPT_MODE, key, algorithmParameters);
            bytes = cipher.doFinal(encryptedDataByte);
        } catch (InvalidParameterSpecException | NoSuchAlgorithmException | NoSuchPaddingException | InvalidKeyException | InvalidAlgorithmParameterException | IllegalBlockSizeException | BadPaddingException e) {
            e.printStackTrace();
            bytes = new byte[0];
        }
        String decryptString = new String(bytes, StandardCharsets.UTF_8);
//        System.out.println(decryptString);
        return decryptString;
    }

    public static void main(String[] args) {
//        String sessionkey="9lpTkQWopRkimol\\/r86Tzg==";
//        String encryptedData="6Vzeih8si7kzRV7ClPnRT0YIRRh0MN7Jh7zjGxjNbAlxx7dstWEisU40pt4Dz70bTQjZ0e4UGb4gLsukTc9DGjZWo/CKv7igpVT0NBfkSbjveNx/qeO/sx8xT8THYNVqjqDqkORrQa78Mo5se2mqmk3c4k3yteBtJ1XF8/RI1Y4QvjMB1vYSnNVHw2yik7kx0TCntcmot+ZJ6AI5/kT6jQ==";
//        String iv="K3hKwl6T8H+FPjCJf66lLQ";

//        code: encryptedData: iv:
        String sessionkey="sxHR06LwpmK+b\\/cRkaclYQ==";
        String encryptedData = "HKiiQIif7C6SSeWFbAlDaAZxbmlmR8bkTBT3mougGJqxjsP39dN/NLT0pw2ub2oeBgWmJkFZ90txA/yWrJqHR/0gT8FiaHfy7OD9uvVKRIo3yElVb0O51GLo51SL1d7MajYRLzsOx0WFb+bP07bH4rjILJm51UdlJmtB7Nt0Jab72O93sD1u1G3MzhR3J3ROP/RoqmK8dTyIIZvIHT8AcQ==";
        String iv="TCT0FrTn2rz02c1i0gUgqg==";
        String s=decryptWeChatData(sessionkey,iv,encryptedData);
        System.out.println(s);
    }
}

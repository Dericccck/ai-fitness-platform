package com.shuyiwa.fitness.backend.util;

import java.security.MessageDigest;
import java.util.UUID;

public class ShortUrlGenerator {

    /** 生成几个短连接地址，可自定义 */
    private final static int COUNT = 1;
    /** 生成几位短连接地址的签名串，可自定义 */
    public final static int LENGTH = 6;

    public static void main(String[] args) {
        String[] shortUrl = shortUrl("https://frog-api.shuyiwa.com/html/formScreen.html?organizationId=40288a8c786433f70178685e9d7003b7&contestSeasonId=40288a8d7868cf5801787d3862670f40");
        for (String s : shortUrl) {
            System.out.println(s);
        }
    }

    /**
     * 获取短连接地址
     * @param url 原字符串
     * @return String
     * @author tyg
     * @date   2019年1月24日下午2:27:24
     */
    public static String getShortUrl(String url) {
        return shortUrl(url)[0];
    }

    /**
     * 返回4组端地址
     * @param url 原串
     * @return String[]
     * @author tyg
     * @date   2019年1月24日下午2:28:36
     */
    private static String[] shortUrl(String url) {
        // 可以自定义生成 MD5 加密字符传前的混合 KEY
        // 要使用生成 URL 的字符
        String[] chars = new String[] { "a", "b", "c", "d", "e", "f", "g", "h",
                "i", "j", "k", "l", "m", "n", "o", "p", "q", "r", "s", "t",
                "u", "v", "w", "x", "y", "z", "0", "1", "2", "3", "4", "5",
                "6", "7", "8", "9", "A", "B", "C", "D", "E", "F", "G", "H",
                "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T",
                "U", "V", "W", "X", "Y", "Z"

        };
        // 对传入网址进行 MD5 加密
        String hex = md5ByHex(UUID.randomUUID().toString() + url);

        String[] resUrl = new String[COUNT];
        for (int i = 0; i < COUNT; i++) {

            // 把加密字符按照 8 位一组 16 进制与 0x3FFFFFFF 进行位与运算
            String sTempSubString = hex.substring(i * 8, i * 8 + 8);

            // 这里需要使用 long 型来转换，因为 Inteper .parseInt() 只能处理 31 位 , 首位为符号位 , 如果不用long ，则会越界
            long lHexLong = 0x3FFFFFFF & Long.parseLong(sTempSubString, 16);
            StringBuilder outChars = new StringBuilder();
            for (int j = 0; j < LENGTH; j++) {
                // 把得到的值与 0x0000003D 进行位与运算，取得字符数组 chars 索引
                long index = 0x0000003D & lHexLong;
                // 把取得的字符相加
                outChars.append(chars[(int) index]);
                // 每次循环按位右移 5 位
                lHexLong = lHexLong >> 5;
            }
            // 把字符串存入对应索引的输出数组
            resUrl[i] = outChars.toString();
        }
        return resUrl;
    }

    /**
     * MD5加密(32位大写)
     * @param src
     * @return String
     * @author tyg
     * @date   2019年5月7日下午6:31:59
     */
    private static String md5ByHex(String src) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] b = src.getBytes();
            md.reset();
            md.update(b);
            byte[] hash = md.digest();
            StringBuilder hs = new StringBuilder();
            String stmp = "";
            for (byte value : hash) {
                stmp = Integer.toHexString(value & 0xFF);
                if (stmp.length() == 1) {
                    hs.append("0").append(stmp);
                } else {
                    hs.append(stmp);
                }
            }
            return hs.toString().toUpperCase();
        } catch (Exception e) {
            return "";
        }
    }

}

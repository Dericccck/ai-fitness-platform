package com.shuyiwa.fitness.backend.util;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.time.LocalDate;

@Component
public class GenCodeUtil {

    @Autowired
    private RedisUtils redisUtils;

    /**
     * 生成KOC编码
     * @param key
     * @return
     */
    public String genKocCode(String key){
        Double v = redisUtils.incr(key,1);
        String vstr = buwei(v.intValue()+"");
        return vstr;
    }


    /**
     * 生成项目编码
     * 年2位，月2位，编码2位，共6位，每个月编码从头开始
     * @param key WH_P_{年2位，月2位}
     * @return
     */
    public String genProjectCode(String key){
        Double v = redisUtils.incr(key,1);
        return getYYMM()+(v.intValue()<10?"0"+v.intValue():v.intValue());
    }

    private static String getYYMM(){
        int yy = LocalDate.now().getYear();
        int mm = LocalDate.now().getMonthValue();
        return String.valueOf(yy).substring(2)+""+(mm<10?"0"+mm:mm);
    }

    public static void main(String[] args) {
        System.out.println(getYYMM());

    }

    /**
     * 生成供应商编码
     * @param key
     * @return
     */
    public String genGongCode(String key){
        Double v = redisUtils.incr(key,1);
        String vstr = buwei(v.intValue()+"");
        return "K-"+vstr;
    }

    /**
     * 生成客户编码
     * @param key
     * @return
     */
    public String genCustomerCode(String key){
        Double v = redisUtils.incr(key,1);
        String vstr = buwei(v.intValue()+"");
        return "K-"+vstr;
    }

    /**
     * 补位，不足5位前面补0
     * @param v
     * @return
     */
    private String buwei(String v){
        String pre="";
        if(v.length()==1){
            pre = "0000";
        }else if(v.length()==2){
            pre = "000";
        }else if(v.length()==3){
            pre = "00";
        }else if(v.length()==4){
            pre = "0";
        }
        return pre+v;
    }




}

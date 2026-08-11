package com.shuyiwa.fitness.backend.domain;


import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonIdentityReference;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.shuyiwa.fitness.backend.web.Const;
import org.hibernate.annotations.GenericGenerator;
import org.springframework.expression.spel.standard.SpelExpressionParser;
import org.springframework.expression.spel.support.StandardEvaluationContext;

import javax.persistence.*;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;

@Entity
public class Sms {
    @Id
    @Column(length = 32)
    @GeneratedValue(generator = "system-uuid")
    @GenericGenerator(name = "system-uuid", strategy = "uuid")
    private String id;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false, insertable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    private Date createTime;

    @Column(nullable = false)
    private String phone;

    @ManyToOne
    @JsonIdentityReference(alwaysAsId = true)
    @JoinColumn
    private MessageTask messageTask;

    @Column(length = 1204)
    private String content;

    @Column
    @Enumerated(EnumType.STRING)
    private Platform platform;

    @Column(length = 20)
    @Enumerated(EnumType.STRING)
    private SmsResult result;

    @Column(length = 1024)
    private String response;

    public static void main(String[] args) throws JsonProcessingException {
        ObjectMapper mapper = new ObjectMapper();
        mapper.configure(SerializationFeature.WRITE_ENUMS_USING_TO_STRING, false);
        Template applySuccessNotify = Template.APPLY_SUCCESS_NOTIFY;
        applySuccessNotify.setProperty("bb", "aa");
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public String getPhone() {
        return phone;
    }

    public void setPhone(String phone) {
        this.phone = phone;
    }

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }

    public Platform getPlatform() {
        return platform;
    }

    public void setPlatform(Platform platform) {
        this.platform = platform;
    }

    public SmsResult getResult() {
        return result;
    }

    public void setResult(SmsResult result) {
        this.result = result;
    }

    public String getResponse() {
        return response;
    }

    public void setResponse(String response) {
        this.response = response;
    }

    public MessageTask getMessageTask() {
        return messageTask;
    }

    public void setMessageTask(MessageTask messageTask) {
        this.messageTask = messageTask;
    }


    public enum Platform {
        /*阿里*/Ali,/*讯迈*/ XunMai
    }

    @JsonFormat(shape = JsonFormat.Shape.OBJECT)
    public enum Template {
//        VerifyCode("SMS_158180034", "\"【树艺蛙】验证码：\"+#code+\"，5分钟内填写有效，请不要告诉其他人。\"", true),
        VerifyCode("SMS_236925884", "\"【Fitooss】验证码：\"+#code+\"，5分钟内填写有效，请不要告诉其他人。\"", true),
        REG_SUCCESS_NOTIFY("SMS_160860770", "\"【树艺蛙】已为您准备了最新的才艺大赛消息与参赛通道！恭喜您注册成功，每天都可以为娃娃们比赛助力哟！\"", false),
        APPLY_SUCCESS_NOTIFY("SMS_218036285", "\"恭喜您，注册成功，请后续下载树艺蛙APP（http://suo.nz/4VK0v0），上传作品完成大赛报名。参与报名页面活动，更可获得晋级、证书、代金券、上电视组合大礼包，详情请在树艺蛙APP内了解。关注“树艺蛙服务”公众号随时了解大赛最新进展。回T退订\"", "\"【树艺蛙】您已成功报名首届“树艺蛙全国少儿才艺大赛”，请下载树艺蛙App，随时关注比赛相关信息，预祝您取得最好的成绩~\"", false, null),
        SMS_169495322("SMS_169495322", "\"辛苦了，才艺大赛最终网络赛制公布，每个星蛙都有机会晋级决赛，快打开树艺蛙了解最新情况，为星蛙最后的冲刺加油把！\"", "\"\"", false, Const.defaultSeasonId),
//        SMS_185240806("SMS_185240806", "\"【树艺蛙】亲爱的\"+#user+\"，： 您的推荐人编号为\"+#code+\"，可用于\"+#contest+\"，相关活动，请妥善保存，祝您工作愉快！。\"", "\"\"", false, Const.defaultSeasonId),
        SMS_189713382("SMS_189713382", "\"【树艺蛙】亲爱的\"+#user+\"，： 您的推荐人编号为\"+#code+\"，请妥善保存，祝您工作愉快！。\"", "\"\"", false, Const.defaultSeasonId),
        SMS_196619870("SMS_196619870", "\"【树艺蛙】亲爱的机构管理员，您的机构\"+#name+\"已注册成功，请到https://org.shuyiwa.com登录进行管理。\"", "\"\"", false, Const.defaultSeasonId),
        SMS_213301599("SMS_213301599", "\"【树艺蛙】您已成为\"+#orgName\"+的管理员，请登录：org.shuyiwa.com网站或“树艺蛙微管理”小程序查看。\"", "\"\"", false, Const.defaultSeasonId),
        SMS_216837191("SMS_216837191", "\"【树艺蛙】\"+#orgName\"+已经建设完成，请登录：org.shuyiwa.com网站或“树艺蛙”微信小程序查看。\"", "\"\"", false, Const.defaultSeasonId);


        private final String aliTemplateCode;
        private final String content;
        private final String xunMaiContent;
        private final boolean onlyAuto;
        private final String contestSeasonId;

        @Transient
        private Map<String, Object> properties = new HashMap<>();

        Template(String aliTemplateCode, String content, boolean onlyAuto) {
            this.aliTemplateCode = aliTemplateCode;
            this.content = content;
            this.xunMaiContent = content;
            this.onlyAuto = onlyAuto;
            this.contestSeasonId = null;
        }

        Template(String aliTemplateCode, String content, String xunMaiContent, boolean onlyAuto, String contestSeasonId) {
            this.aliTemplateCode = aliTemplateCode;
            this.content = content;
            this.onlyAuto = onlyAuto;
            this.xunMaiContent = xunMaiContent;
            this.contestSeasonId = contestSeasonId;
        }

        public String getResolvedContent(HashMap<String, String> params, String content) {
            if (content == null) {
                return null;
            }
            StandardEvaluationContext standardEvaluationContext = new StandardEvaluationContext();
            params.forEach((k, v) -> standardEvaluationContext.setVariable(k, v));
            SpelExpressionParser parser = new SpelExpressionParser();
            Object value = parser.parseExpression(content).getValue(standardEvaluationContext);
            return "" + value;

        }

        public String getResolvedContent(HashMap<String, String> params) {
            return getResolvedContent(params, content);
        }

        public String getAliTemplateCode() {
            return aliTemplateCode;

        }

        public String getContestSeasonId() {
            return contestSeasonId;
        }

        public boolean isOnlyAuto() {
            return onlyAuto;
        }

        public String getContent() {
            return content;
        }

        public String getXunMaiContent() {
            return xunMaiContent;
        }

        @JsonAnyGetter
        public Map<String, Object> getProperties() {
            return properties;
        }

        @JsonAnySetter
        public void setProperty(String name, Object value) {
            properties.put(name, value);
        }
    }

    public enum SmsResult {
        SUCCESS, FAIL, EXCEPTION, IGNORE
    }
}

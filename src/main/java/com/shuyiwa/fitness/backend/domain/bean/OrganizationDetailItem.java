package com.shuyiwa.fitness.backend.domain.bean;

public class OrganizationDetailItem {
    private String id;
    private String dataType;  //TEXT:文本  IMG:图片  VIDEO:视频
    private String content;
    private Integer publicState;   //0:新增未发布  1：修改未发布  2：变换位置未发布   10：已发布

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getDataType() {
        return dataType;
    }

    public void setDataType(String dataType) {
        this.dataType = dataType;
    }

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }

    public Integer getPublicState() {
        return publicState;
    }

    public void setPublicState(Integer publicState) {
        this.publicState = publicState;
    }
}

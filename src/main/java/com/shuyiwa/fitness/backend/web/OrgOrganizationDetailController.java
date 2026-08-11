package com.shuyiwa.fitness.backend.web;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.LoginUserFile;
import com.shuyiwa.fitness.backend.domain.LoginUserFileRepository;
import com.shuyiwa.fitness.backend.domain.Organization;
import com.shuyiwa.fitness.backend.domain.OrganizationRepository;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.third.aliyun.service.AliyunPicService;

import com.shuyiwa.fitness.backend.domain.bean.OrganizationDetailItem;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.*;

import java.io.File;
import java.io.IOException;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.stream.Collectors;

@RestController
public class OrgOrganizationDetailController {

    @Autowired
    OrganizationRepository organizationRepository;
    @Autowired
    LoginUserFileRepository loginUserFileRepository;
    @Autowired
    AliyunPicService picService;

    private ObjectMapper objectMapper = new ObjectMapper();

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "机构详情ITEM列表")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/org/detail/item", method = RequestMethod.GET)
    List<OrganizationDetailItem> orgDetailItem(
            @RequestParam("organizationId") String organizationId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) {
        Organization organization = organizationRepository.findById(organizationId).orElse(null);
        if(organization==null || StringUtils.isEmpty(organization.getDetailBodyRaw())){
            return null;
        }
        try {
            List<OrganizationDetailItem> organizationDetailItems = objectMapper.readValue(organization.getDetailBodyRaw(),ArrayList.class);
            return organizationDetailItems;
        } catch (IOException e) {
            e.printStackTrace();
        }
        return null;
    }



    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "删除机构详情ITEM")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/org/detail/item", method = RequestMethod.DELETE)
    void deleteOrgDetailItem(
            @RequestParam("id") String id,
            @RequestParam("organizationId") String organizationId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {

        Organization organization = organizationRepository.findById(organizationId).orElse(null);
        if(organization==null || StringUtils.isEmpty(organization.getDetailBodyRaw())){
            return ;
        }
        try {
            List<OrganizationDetailItem> organizationDetailItems=objectMapper.readValue(organization.getDetailBodyRaw(),new TypeReference<ArrayList<OrganizationDetailItem>>(){});
//            List<OrganizationDetailItem> organizationDetailItems = objectMapper.readValue(organization.getDetailBodyRaw(),ArrayList.class);
            organizationDetailItems = organizationDetailItems.stream().filter(organizationDetailItem -> !organizationDetailItem.getId().equals(id)).collect(Collectors.toList());
            organization.setDetailBodyRaw(objectMapper.writeValueAsString(organizationDetailItems));
            organizationRepository.save(organization);
        } catch (IOException e) {
            e.printStackTrace();
            throw new FrogException(1000,"删除失败，请联系管理员!");
        }
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "新增/修改机构详情ITEM列表")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/org/detail/item", method = RequestMethod.POST)
    OrganizationDetailItem saveOrgDetailItem(@RequestParam("organizationId") String organizationId, @RequestBody OrganizationDetailItem organizationDetailItem, @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        Organization organization = organizationRepository.findById(organizationId).orElse(null);
        if(organization==null){
            return null;
        }
        try {
            if(StringUtils.isEmpty(organizationDetailItem.getId())){
                organizationDetailItem.setId(UUID.randomUUID().toString().replaceAll("-",""));
            }
            //图片处理
            if("IMG".equals(organizationDetailItem.getDataType())){
                String imgId = organizationDetailItem.getContent();
                if (!org.apache.commons.lang.StringUtils.isEmpty(imgId)) {
                    LoginUserFile loginUserFile = loginUserFileRepository.findById(imgId).orElse(null);
                    if (loginUserFile != null) {
                        File file = new File(loginUserFile.getPath());
                        if (file.exists()) {
                            String picUrl = picService.uploadPic("orgDetail", file);
                            organizationDetailItem.setContent(picUrl);
                        }
                        loginUserFile.setRemoved(true);
                        loginUserFileRepository.save(loginUserFile);
                    }
                }
            }
            if(StringUtils.isEmpty(organization.getDetailBodyRaw())){
                List<OrganizationDetailItem> organizationDetailItems = new ArrayList<>();
                organizationDetailItems.add(organizationDetailItem);
                organizationDetailItem.setPublicState(0);
                organization.setDetailBodyRaw(objectMapper.writeValueAsString(organizationDetailItems));
                organizationRepository.save(organization);
                return organizationDetailItem;
            }
            List<OrganizationDetailItem> organizationDetailItems=objectMapper.readValue(organization.getDetailBodyRaw(),new TypeReference<ArrayList<OrganizationDetailItem>>(){});
//            List<OrganizationDetailItem> organizationDetailItems = objectMapper.readValue(organization.getDetailBodyRaw(),ArrayList.class);
            AtomicBoolean isUpdate = new AtomicBoolean(false);
            organizationDetailItems.stream().filter(odi -> odi.getId().equals(organizationDetailItem.getId())).forEach(odi -> {
                odi.setContent(organizationDetailItem.getContent());
                odi.setPublicState(1);
                organizationDetailItem.setPublicState(1);
                isUpdate.set(true);
            });
            if(!isUpdate.get()){
                organizationDetailItem.setPublicState(0);
                organizationDetailItems.add(organizationDetailItem);
            }

            organization.setDetailBodyRaw(objectMapper.writeValueAsString(organizationDetailItems));
            organizationRepository.save(organization);
        } catch (IOException e) {
            e.printStackTrace();
            throw new FrogException(1000,"修改失败，请联系管理员!");
        }

        return organizationDetailItem;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "上移机构详情ITEM")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/org/detail/item/up", method = RequestMethod.POST)
    void upOrgDetailItem(@RequestParam("organizationId") String organizationId, @RequestParam("organizationDetailItemId") String organizationDetailItemId,@AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        Organization organization = organizationRepository.findById(organizationId).orElse(null);
        if(organization==null || StringUtils.isEmpty(organization.getDetailBodyRaw())){
            return ;
        }
        try {

            List<OrganizationDetailItem> organizationDetailItems=objectMapper.readValue(organization.getDetailBodyRaw(),new TypeReference<ArrayList<OrganizationDetailItem>>(){});
            if(organizationDetailItems!=null && organizationDetailItems.size()>0){
                for(int i=0; i<organizationDetailItems.size(); i++){
                    OrganizationDetailItem orgDetail = organizationDetailItems.get(i);
                    if(orgDetail.getId().equals(organizationDetailItemId)){
                        if(i>0){
                            organizationDetailItems.remove(orgDetail);
                            orgDetail.setPublicState(2);
                            organizationDetailItems.add(i-1,orgDetail);
                            organization.setDetailBodyRaw(objectMapper.writeValueAsString(organizationDetailItems));
                            organizationRepository.save(organization);
                        }
                        break;
                    }
                }
            }


        } catch (IOException e) {
            e.printStackTrace();
            throw new FrogException(1000,"修改失败，请联系管理员!");
        }

    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "下移机构详情ITEM")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/org/detail/item/down", method = RequestMethod.POST)
    void downOrgDetailItem(@RequestParam("organizationId") String organizationId, @RequestParam("organizationDetailItemId") String organizationDetailItemId,@AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        Organization organization = organizationRepository.findById(organizationId).orElse(null);
        if(organization==null || StringUtils.isEmpty(organization.getDetailBodyRaw())){
            return ;
        }
        try {

            List<OrganizationDetailItem> organizationDetailItems=objectMapper.readValue(organization.getDetailBodyRaw(),new TypeReference<ArrayList<OrganizationDetailItem>>(){});
            if(organizationDetailItems!=null && organizationDetailItems.size()>0){
                for(int i=0; i<organizationDetailItems.size(); i++){
                    OrganizationDetailItem orgDetail = organizationDetailItems.get(i);
                    if(orgDetail.getId().equals(organizationDetailItemId)){
                        if(i<organizationDetailItems.size()-1){
                            organizationDetailItems.remove(orgDetail);
                            orgDetail.setPublicState(2);
                            organizationDetailItems.add(i+1,orgDetail);
                            organization.setDetailBodyRaw(objectMapper.writeValueAsString(organizationDetailItems));
                            organizationRepository.save(organization);
                        }
                        break;
                    }
                }
            }


        } catch (IOException e) {
            e.printStackTrace();
            throw new FrogException(1000,"修改失败，请联系管理员!");
        }

    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "新增/修改机构详情")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/org/detail/allitem", method = RequestMethod.POST)
    OrganizationDetailItem saveOrgDetail(@RequestParam("organizationId") String organizationId, @RequestBody OrganizationDetailItem organizationDetailItem, @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        Organization organization = organizationRepository.findById(organizationId).orElse(null);
        if(organization==null ){
            return null;
        }
        try {
            organizationDetailItem.setPublicState(10);
            organizationDetailItem.setDataType("TEXT");
            organizationDetailItem.setId(UUID.randomUUID().toString().replaceAll("-",""));
            List<OrganizationDetailItem> organizationDetailItems = new ArrayList<>();
            organizationDetailItems.add(organizationDetailItem);

            organization.setDetailBodyRaw(objectMapper.writeValueAsString(organizationDetailItems));
            organization.setDetailBody("<p>"+organizationDetailItem.getContent()+"</p>");
            organizationRepository.save(organization);
        } catch (IOException e) {
            e.printStackTrace();
            throw new FrogException(1000,"修改失败，请联系管理员!");
        }

        return organizationDetailItem;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Org, desc = "发布机构详情")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/org/detail/item/pub", method = RequestMethod.POST)
    void publicDetail(@RequestParam("organizationId") String organizationId, @AuthenticationPrincipal FrogUserDetails frogUserDetails) throws FrogException {
        Organization organization = organizationRepository.findById(organizationId).orElse(null);
        if(organization==null ){
            return ;
        }
        try {
            if(StringUtils.isEmpty(organization.getDetailBodyRaw())){
                organization.setDetailBody("");
                organizationRepository.save(organization);
            }
            List<OrganizationDetailItem> organizationDetailItems=objectMapper.readValue(organization.getDetailBodyRaw(),new TypeReference<ArrayList<OrganizationDetailItem>>(){});
            if(organizationDetailItems==null || organizationDetailItems.isEmpty()){
                organization.setDetailBody("");
                organizationRepository.save(organization);
            }
            StringBuffer body = new StringBuffer();
            for (OrganizationDetailItem organizationDetailItem : organizationDetailItems){
                String dataType = organizationDetailItem.getDataType();
                switch (dataType){
                    case "TEXT" : body.append("<p>"+organizationDetailItem.getContent()+"</p>");break;
                    case "IMG" : body.append("<p><img src=\""+organizationDetailItem.getContent()+"\"></p>");break;
                    default : break;
                }
                organizationDetailItem.setPublicState(10);
            }

            organization.setDetailBody(body.toString());
            organization.setDetailBodyRaw(objectMapper.writeValueAsString(organizationDetailItems));
            organizationRepository.save(organization);

        } catch (IOException e) {
            e.printStackTrace();
            throw new FrogException(1000,"修改失败，请联系管理员!");
        }

        return ;
    }


}

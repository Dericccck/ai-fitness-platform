package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.LoginUserRepository;
import com.shuyiwa.fitness.backend.service.LoginUserFileService;
import com.shuyiwa.fitness.backend.domain.LoginUserFile;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.repository.query.Param;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseBody;
import org.springframework.web.multipart.MultipartFile;

import javax.servlet.http.HttpServletRequest;
import java.io.File;
import java.io.InputStream;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.List;

@Controller
@RequestMapping("api/file")
public class FileController {
    private static final Log logger = LogFactory.getLog(FileController.class);

    @Autowired
    LoginUserFileService loginUserFileService;
    @Autowired
    LoginUserRepository loginUserRepository;


    //    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = {RuntimeDoc.Client.Console, RuntimeDoc.Client.Api}, desc = "上传文件")
    @RequestMapping(value = "upload", method = RequestMethod.POST)
    @ResponseBody
    public LoginUserFile upload(
            @RequestParam(value = "file") MultipartFile file,
            @RequestParam(value = "accept", required = false) String accept,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @Param("useType") String useType,
            HttpServletRequest request) throws FrogException {
        if (accept != null && accept.equals("mp4,jpg,jpeg,png,gif")) {//TODO: 临时补充
            accept = accept + ",mov";
        }
        return loginUserFileService.save(file, frogUserDetails == null ? null : frogUserDetails.getLoginUser(loginUserRepository), useType, accept);
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "获取已上传文件")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(method = RequestMethod.GET)
    @ResponseBody
    public List<LoginUserFile> findLoginUserFiles(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @Param("useType") String useType,
            HttpServletRequest request) throws FrogException {
        return loginUserFileService.findLoginUserFiles(frogUserDetails.getLoginUser(loginUserRepository), useType);
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Console}, desc = "删除已上传文件")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(method = RequestMethod.DELETE)
    @ResponseBody
    public void delLoginUserFiles(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @Param("id") String id,
            HttpServletRequest request) throws FrogException {
        loginUserFileService.del(id);
    }




    private static void downloadImgFrowWeb(String imgUrl) throws Exception {
        InputStream in = new URL("https://images0.cnblogs.com/blog2015/614265/201506/301329152904248.jpg").openStream();
        Path temp = Paths.get("temp.png");
        Files.copy(in,temp, StandardCopyOption.REPLACE_EXISTING);
        File file = temp.toFile();
        System.out.println(file.getAbsolutePath());

    }
}

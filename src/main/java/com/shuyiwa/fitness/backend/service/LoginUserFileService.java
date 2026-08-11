package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.event.WorksUploadEvent;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.third.aliyun.service.AliyunPicService;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.apache.tika.Tika;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.FileCopyUtils;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;

import javax.persistence.EntityManager;
import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.text.SimpleDateFormat;
import java.util.*;

import static com.shuyiwa.fitness.backend.global.FrogException.*;

@Service
public class LoginUserFileService {
    private static final Log logger = LogFactory.getLog(LoginUserFileService.class);

    @Value("${com.shuyiwa.fitness.backend.upload-dir:upload-dir}")
    String uploadDir;
    @Value("${com.shuyiwa.fitness.backend.works-dir:works-dir}")
    String worksDir;
    @Value("${com.shuyiwa.fitness.backend.user-avatar-dir:user-avatar-dir}")
    String userAvatarDir;
    @Value("${com.shuyiwa.fitness.backend.organization-logo-dir:organization-logo-dir}")
    String organizationLogoDir;
    @Value("${com.shuyiwa.fitness.backend.contest-schedule-logo-dir:contest-schedule-logo-dir}")
    String contestScheduleLogoDir;
    @Value("${com.shuyiwa.fitness.backend.article-image-dir:article-image-dir}")
    String articleImageDir;
    @Value("${com.shuyiwa.fitness.backend.app-store-dir:app-store-dir}")
    String appStoreDir;
    @Value("${com.shuyiwa.fitness.backend.disk.url.suffix:https://console.fitooss.com/pass}")
    String diskUrl;


    @Autowired
    LoginUserFileRepository loginUserFileRepository;
    @Autowired
    EntityManager entityManager;
    @Autowired
    WorksRepository worksRepository;
    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    WorksService worksService;
    @Autowired
    AliyunPicService aliyunPicService;
    @Autowired
    ArticleImageRepository articleImageRepository;
    @Autowired
    OrganizationRepository organizationRepository;
    @Autowired
    ContestScheduleRepository contestScheduleRepository;
    @Autowired
    private ApplicationEventPublisher applicationEventPublisher;

    public static String getSuffix(String originalFilename, File file, HashSet<String> acceptSet, LoginUserFile loginUserFile) throws FrogException {
        //如果文件名有后缀,且后缀长度未19位以内，则直接使用
        if (originalFilename != null && originalFilename.indexOf(".") > 0) {
            String suffix = originalFilename.replaceAll(".*\\.", "").toLowerCase();
            if (suffix.length() < 19 && (acceptSet.size() == 0 || acceptSet.contains(suffix))) {
                return "." + suffix;
            }
        }
        //如果没有合适的后缀，则根据文件类型生成猴嘴
        Tika tika = new Tika();
        try {
            String detect = tika.detect(file);
            String suffix = detect.replaceAll(".*/", "").toLowerCase();
            if (suffix.equals("quicktime")) {
                suffix = "mov";
            }
            if (acceptSet.size() == 0 || acceptSet.contains(suffix)) {
                loginUserFile.setContentType(detect);
                return "." + suffix;
            } else {
                throw new FrogException(FORBIDDEN, "未识别的文件类型:" + originalFilename + ",." + suffix);
            }
        } catch (IOException e) {
            throw new FrogException(FORBIDDEN, "未识别的文件类型:" + originalFilename);
        }
    }

    public LoginUserFile getFromDiskUrl(String diskUrl) {
        if (diskUrl == null) {
            return null;
        }
        if (!diskUrl.startsWith("https://console.fitooss.com/pass/disk")
                && !diskUrl.startsWith("http://localhost:3000/pass/disk")) {
            return null;
        }
        String filename = diskUrl.substring(diskUrl.lastIndexOf("/") + 1);
        int i = filename.indexOf(".");
        String id = i > 0 ? filename.substring(0, i) : filename;
        return loginUserFileRepository.findById(id).orElse(null);
    }

    @Transactional(rollbackFor = Throwable.class)
    public LoginUserFile save(MultipartFile file, LoginUser loginUser, String useType, String accept) throws FrogException {
        HashSet<String> acceptSet = new HashSet<>();
        if (accept != null) {
            Arrays.stream(accept.toLowerCase().split(",")).forEach(type -> acceptSet.add(type));
        }
        LoginUserFile loginUserFile = new LoginUserFile();
        loginUserFile.setName(file.getName());
        logger.info("file.getOriginalFilename()):" + file.getOriginalFilename());
        loginUserFile.setOriginalFilename(getFileName(file.getOriginalFilename()));
        logger.info("loginUserFile.getOriginalFilename()):" + loginUserFile.getOriginalFilename());
        File tmpFile;
        try {
//            Path  tmpFilepath = Files.createTempFile(loginUserFile.getOriginalFilename() + System.currentTimeMillis(),"tmp");
//            file.transferTo(tmpFilepath);
//            tmpFile = tmpFilepath.toFile();
            tmpFile = File.createTempFile(loginUserFile.getOriginalFilename() + System.currentTimeMillis(), ".tmp");
            file.transferTo(tmpFile);
        } catch (IOException e) {
            e.printStackTrace();
            throw new FrogException(INTERNAL_SERVER_ERROR, "上传文件保存失败,空间不足");
        }
        loginUserFile.setContentType(file.getContentType());
        logger.info("originalFilename:" + file.getOriginalFilename());
        loginUserFile.setSuffix(getSuffix(file.getOriginalFilename(), tmpFile, acceptSet, loginUserFile));
        loginUserFile.setSize(file.getSize());
        loginUserFile.setLoginUser(loginUser);
        loginUserFile.setUseType(useType);
        loginUserFileRepository.save(loginUserFile);
        entityManager.flush();
        entityManager.refresh(loginUserFile);
        File currentDir = new File(new SimpleDateFormat("yyyy-MM-dd").format(loginUserFile.getCreateTime()));
        try {
            new File(uploadDir, currentDir.getPath()).mkdirs();
            File dest = new File(currentDir, loginUserFile.getId() + loginUserFile.getSuffix());
            loginUserFile.setDiskUrl(diskUrl + "/disk/upload/" + dest.getPath());
            loginUserFile.setPath(new File(uploadDir, dest.getPath()).getCanonicalPath());
            FileCopyUtils.copy(tmpFile, new File(loginUserFile.getPath()));
            tmpFile.delete();
            if ("multipart/form-data".equals(file.getContentType())) {
                logger.info("change content type of 程磊");
                //程磊上传文件，这时候需要修正contentType
                String contentType = Files.probeContentType(new File(loginUserFile.getPath()).toPath());
                loginUserFile.setContentType(contentType);
            }
            loginUserFileRepository.save(loginUserFile);
            return loginUserFile;
        } catch (IOException e) {
            throw new FrogException(SAVE_UPLOAD_FILE_FAILED, "上传文件保存失败", e);
        }

    }

    private String getFileName(String originalFilename) {
        if (StringUtils.isEmpty(originalFilename)) {
            return originalFilename;
        }
        return new File(originalFilename).getName();
    }

    @Transactional(rollbackFor = Throwable.class)
    public List<LoginUserFile> findLoginUserFiles(LoginUser loginUser, String useType) throws FrogException {
        Date date = new Date();
        Calendar calendar = Calendar.getInstance();
        calendar.setTime(date);
        calendar.add(Calendar.DAY_OF_MONTH, -3);
        return loginUserFileRepository.findByLoginUserAndUseTypeAndRemovedAndCreateTimeAfter(loginUser, useType, false, calendar.getTime());
    }

    public void use(LoginUserFile loginUserFile, Organization organization) throws FrogException {
        organizationRepository.save(organization);
        entityManager.flush();
        entityManager.refresh(organization);
        File currentDir = new File(new SimpleDateFormat("yyyy-MM-dd").format(organization.getCreateTime()));
        new File(new File(organizationLogoDir), currentDir.getPath()).mkdirs();
        try {
            new File(organizationLogoDir, currentDir.getPath()).mkdirs();
            File dest = new File(currentDir, organization.getId() + loginUserFile.getSuffix());
            organization.setLogoDiskUrl(diskUrl + "/disk/organization/logo/" + dest.getPath());
            organization.setLogoPath(new File(organizationLogoDir, dest.getPath()).getCanonicalPath());
            FileCopyUtils.copy(new File(loginUserFile.getPath()), new File(organization.getLogoPath()));
            String avatarUrl = aliyunPicService.uploadPic("organization", new File(organization.getLogoPath()));
            organization.setLogo(avatarUrl);
            organizationRepository.save(organization);
            loginUserFile.setRemoved(true);
            loginUserFileRepository.save(loginUserFile);
        } catch (IOException e) {
            throw new FrogException(SAVE_UPLOAD_FILE_FAILED, "保存作品logo失败", e);
        }
    }

    public void use(LoginUserFile loginUserFile, ContestSchedule contestSchedule) throws FrogException {
        contestScheduleRepository.save(contestSchedule);
        entityManager.flush();
        if (contestSchedule.getCreateTime() == null) {
            entityManager.refresh(contestSchedule);
        }
        File currentDir = new File(new SimpleDateFormat("yyyy-MM-dd").format(contestSchedule.getCreateTime()));
        try {
            new File(contestScheduleLogoDir, currentDir.getPath()).mkdirs();
            File dest = new File(currentDir, contestSchedule.getId() + loginUserFile.getSuffix());
            FileCopyUtils.copy(new File(loginUserFile.getPath()), new File(contestScheduleLogoDir, dest.getPath()));
            String url = aliyunPicService.uploadPic("contestSchedule", new File(loginUserFile.getPath()));
            contestSchedule.setLogo(url);
            contestScheduleRepository.save(contestSchedule);
            loginUserFile.setRemoved(true);
            loginUserFileRepository.save(loginUserFile);
            entityManager.flush();
        } catch (IOException e) {
            throw new FrogException(SAVE_UPLOAD_FILE_FAILED, "保存赛程logo失败", e);
        }
    }

    public Works newUse(LoginUserFile loginUserFile, Works works) throws FrogException {
        if (works.getName() == null) {
            String name = loginUserFile.getOriginalFilename();
            if (name != null) {
                name = name.replaceAll("\\..*", "");
            }
            works.setName(name);
        }
        works.setFormat(Works.WorksFormat.fromUploadType(loginUserFile.getContentType()));
        if (works.getCreateTime() == null) {
            works = worksService.save(works);
            entityManager.flush();
            entityManager.refresh(works);
        }
        works.setScore(works.getCreateTime().getTime());
//        works = worksService.save(works);
        File currentDir = new File(new SimpleDateFormat("yyyy-MM-dd").format(works.getCreateTime()));
        try {
            new File(worksDir, currentDir.getPath()).mkdirs();
            works.setSuffix(loginUserFile.getSuffix());
            File dest = new File(currentDir, works.getId() + works.getSuffix());
            works.setDiskUrl(diskUrl + "/disk/works/" + dest.getPath());
            works.setPath(new File(worksDir, dest.getPath()).getCanonicalPath());
            FileCopyUtils.copy(new File(loginUserFile.getPath()), new File(works.getPath()));
            if (works.getFormat() == Works.WorksFormat.IMG) {
                String avatarUrl = aliyunPicService.uploadPic("works", new File(works.getPath()));
                works.setCoverUrl(avatarUrl);
                works.setStatus(Works.WorksStatus.SUCCEEDED);
            }
            works = worksService.save(works);
            WorksUploadEvent event = new WorksUploadEvent();
            event.setWorks(works);
            applicationEventPublisher.publishEvent(event);

            loginUserFile.setRemoved(true);
            loginUserFileRepository.save(loginUserFile);
            return works;
        } catch (IOException e) {
            throw new FrogException(SAVE_UPLOAD_FILE_FAILED, "上传失败", e);
        }
    }


    public void use(String avatarFileId, LoginUser loginUserInDb) throws FrogException {
        logger.info("save login user3:avatarFileId:" + avatarFileId);
        if (avatarFileId != null) {
            logger.info("save login user4:avatarFileId:" + avatarFileId);
            Optional<LoginUserFile> userFileOptional = loginUserFileRepository.findById(avatarFileId);
            if (userFileOptional.isPresent()) {
                logger.info("save login user5:avatarFileId:" + avatarFileId);
                LoginUserFile loginUserFile = userFileOptional.get();
                File currentDir = new File(new SimpleDateFormat("yyyy-MM-dd").format(loginUserInDb.getCreateTime()));
                try {
                    logger.info("save login user6:avatarFileId:" + avatarFileId);
                    new File(userAvatarDir, currentDir.getPath()).mkdirs();
                    File dest = new File(currentDir, loginUserInDb.getId() + loginUserFile.getSuffix());
                    loginUserInDb.setAvatarPath(new File(userAvatarDir, dest.getPath()).getCanonicalPath());
                    loginUserInDb.setAvatarDiskUrl(diskUrl + "/disk/user/avatar/" + dest.getPath());
                    FileCopyUtils.copy(new File(loginUserFile.getPath()), new File(loginUserInDb.getAvatarPath()));
                    String avatarUrl = aliyunPicService.uploadPic("avatar", new File(loginUserInDb.getAvatarPath()));
                    loginUserInDb.setAvatar(avatarUrl);
                    loginUserRepository.save(loginUserInDb);

                    loginUserFile.setRemoved(true);
                    loginUserFileRepository.save(loginUserFile);
                    logger.info("save login user7:avatarFileId:" + avatarFileId);
                } catch (IOException e) {
                    throw new FrogException(SAVE_UPLOAD_FILE_FAILED, "上传失败", e);
                }
            }

        }
    }

    @Transactional
    public void use(LoginUserFile loginUserFile, ArticleImage articleImage, Date createTime) throws FrogException {
        articleImageRepository.save(articleImage);
        entityManager.flush();
        entityManager.refresh(articleImage);
        File currentDir = new File(new File(new SimpleDateFormat("yyyy-MM-dd").format(createTime), articleImage.getEntityType().name()), articleImage.getEntityId());
//        File currentDir = new File(new SimpleDateFormat("yyyy-MM-dd").format(article.getCreateTime()), article.getId());
        try {
            new File(articleImageDir, currentDir.getPath()).mkdirs();
            File dest = new File(currentDir, articleImage.getId() + loginUserFile.getSuffix());
            articleImage.setPath(new File(articleImageDir, dest.getPath()).getCanonicalPath());
            articleImage.setDiskUrl(diskUrl + "/disk/article/image/" + dest.getPath());
            FileCopyUtils.copy(new File(loginUserFile.getPath()), new File(articleImage.getPath()));
            String url = aliyunPicService.uploadPic("article", new File(articleImage.getPath()));
            articleImage.setUrl(url);
            articleImageRepository.save(articleImage);

            loginUserFile.setRemoved(true);
            loginUserFileRepository.save(loginUserFile);
        } catch (IOException e) {
            throw new FrogException(SAVE_UPLOAD_FILE_FAILED, "上传失败", e);
        }
    }

    @Transactional
    public void use(LoginUserFile loginUserFile, ArticleImage articleImage, Article article) throws FrogException {
        articleImageRepository.save(articleImage);
        entityManager.flush();
        entityManager.refresh(articleImage);
        File currentDir = new File(new SimpleDateFormat("yyyy-MM-dd").format(article.getCreateTime()), article.getId());
        try {
            new File(articleImageDir, currentDir.getPath()).mkdirs();
            File dest = new File(currentDir, articleImage.getId() + loginUserFile.getSuffix());
            articleImage.setPath(new File(articleImageDir, dest.getPath()).getCanonicalPath());
            articleImage.setDiskUrl(diskUrl + "/disk/article/image/" + dest.getPath());
            FileCopyUtils.copy(new File(loginUserFile.getPath()), new File(articleImage.getPath()));
            String url = aliyunPicService.uploadPic("article", new File(articleImage.getPath()));
            articleImage.setUrl(url);
            articleImageRepository.save(articleImage);

            loginUserFile.setRemoved(true);
            loginUserFileRepository.save(loginUserFile);
        } catch (IOException e) {
            throw new FrogException(SAVE_UPLOAD_FILE_FAILED, "上传失败", e);
        }
    }

    @Transactional
    public void del(String id) throws FrogException {
        LoginUserFile loginUserFile = loginUserFileRepository.findById(id).orElse(null);
        if (loginUserFile == null) {
            throw new FrogException(FrogException.DEL_UPLOAD_FILE_FAILED, "the loginUserFile[id:" + id + "] is not exist ! ");
        }
        loginUserFile.setRemoved(true);
        loginUserFileRepository.save(loginUserFile);
    }

    public void saveToAppStore(String loginUserFileId) throws FrogException {
        Optional<LoginUserFile> userFileOptional = loginUserFileRepository.findById(loginUserFileId);
        logger.info("saveToAppStore");
        if (userFileOptional.isPresent()) {
            LoginUserFile loginUserFile = userFileOptional.get();
            String name = new File(loginUserFile.getOriginalFilename()).getName();
            logger.info("saveToAppStore:" + name);
            try {
                new File(appStoreDir).mkdirs();
                FileCopyUtils.copy(new File(loginUserFile.getPath()), new File(appStoreDir, name));
            } catch (IOException e) {
                throw new FrogException(SAVE_UPLOAD_FILE_FAILED, "上传失败", e);
            }
        }
    }

    public List<Map<String, Object>> listAppStore() {
        File root = new File(appStoreDir);
        List<Map<String, Object>> files = new ArrayList<>();
        File[] listFiles = root.listFiles();
        if (listFiles != null) {
            Arrays.stream(listFiles).forEach(file -> {
                if (file.isFile()) {
                    Map<String, Object> map = new HashMap<>();
                    map.put("name", file.getName());
                    map.put("lastModified", file.lastModified());
                    map.put("length", file.length());
                    files.add(map);
                }
            });
        }
        return files;
    }

    public boolean deleteFileFromAppStore(String name) {
        return new File(appStoreDir, name).delete();
    }

    public String upload(String objectType, LoginUserFile loginUserFile) throws FrogException {
        try {
            return aliyunPicService.uploadPic(objectType, new File(loginUserFile.getPath()));
        } catch (FrogException e) {
            throw new FrogException(SAVE_UPLOAD_FILE_FAILED, "上传图片失败", e);
        }
    }
}

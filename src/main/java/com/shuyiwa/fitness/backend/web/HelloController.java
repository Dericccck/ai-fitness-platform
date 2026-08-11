package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.conf.ResubmitLock;
import com.shuyiwa.fitness.backend.conf.CacheParam;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.dict.Authority;
import com.shuyiwa.fitness.backend.service.*;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.dict.ContestantType;
import com.shuyiwa.fitness.backend.global.FrogException;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.apache.poi.ss.usermodel.CellType;
import org.apache.poi.xssf.usermodel.XSSFCell;
import org.apache.poi.xssf.usermodel.XSSFRow;
import org.apache.poi.xssf.usermodel.XSSFSheet;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.*;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.math.BigDecimal;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import static com.shuyiwa.fitness.backend.Utils.withName;

@RestController
public class HelloController {
    private static final Log logger = LogFactory.getLog(HelloController.class);

    @Autowired
    WarnService warnService;

    @Autowired
    LoginUserRepository loginUserRepository;
    @Autowired
    UserTaskService userTaskService;
    @Autowired
    ContestService contestService;
    @Autowired
    LoginUserAuthorityRepository loginUserAuthorityRepository;
    @Autowired
    LoginUserService loginUserService;
    @Autowired
    OrganizationRepository organizationRepository;
    @Autowired
    LoginUserRankInContestSeasonService loginUserRankInContestSeasonService;
    @Autowired
    MessageService messageService;
    @Autowired
    CertificateService certificateService;
    @Autowired
    WorksRepository worksRepository;
    @Autowired
    LoginUserTaskProgressRepository loginUserTaskProgressRepository;
    @Autowired
    LoginUserTaskRepository loginUserTaskRepository;
    @Autowired
    DevicePushInstanceRepository devicePushInstanceRepository;
    //    @Autowired
//    ARepository aRepository;
    @Autowired
    ContestantInfoRepository contestantInfoRepository;
    @Autowired
    ContestScheduleRepository contestScheduleRepository;


    @Transactional(rollbackFor = Throwable.class)
    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "发送消息")
    @RequestMapping(value = "api/tools/user/message/by/phone", method = RequestMethod.POST)
    public UserMessage sendMessage(@RequestBody String content, @RequestParam("phone") String phone) throws FrogException {
        LoginUser loginUser = loginUserRepository.findByPhone(phone).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "用户不存在"));

        UserMessage userMessage = new UserMessage();
        userMessage.setLoginUser(loginUser);
        userMessage.setMessageType(UserMessage.MessageType.USER);
        userMessage.setContent(content);
        messageService.saveUserMessage(userMessage);
        return userMessage;
    }

    @Transactional(rollbackFor = Throwable.class)
    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "测试时间")
    @RequestMapping(value = "api/tools/now", method = RequestMethod.GET)
    public Map<String, Object> now() {
        Map<String, Object> map = new HashMap<>();
        map.put("now", contestScheduleRepository.now());
        map.put("now2", new Date());
        map.put("now13", contestScheduleRepository.now().getTime());
        map.put("now23", new Date().getTime());
        map.put("now4", System.currentTimeMillis());
        return map;

    }

    @Autowired
    ArticleRepository articleRepository;
    @Autowired
    ContestSeasonRepository contestSeasonRepository;

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "修改活动详情文章body_raw里的img")
    @RequestMapping(value = "api/tools/activity/article/update/local/img", method = RequestMethod.GET)
    public void updateActivityArticleImage() {
        List<ContestSeason> all = contestSeasonRepository.findAll(Specification.where(null));
        List<ContestSeason> batch = new ArrayList<>();
        for (ContestSeason contestSeason : all) {
            String bodyRaw = contestSeason.getBodyRaw();
            String body = contestSeason.getBody();
            String regex = "https://img.shuyiwa.com/contestSeason[^\"]*";
            String[] array = findAll(body, regex);

            String regexRaw = "https://frog-console.shuyiwa.com/pass/disk/upload[^\"]*";
            String[] arrayRaw = findAll(bodyRaw, regexRaw);

            if (array.length == arrayRaw.length) {
                for (int i = 0; i < array.length; i++) {
                    bodyRaw = bodyRaw.replace(arrayRaw[i], array[i]);
                }
            }
            contestSeason.setBodyRaw(bodyRaw);
            logger.error("contestSeason:equal:" + (array.length == arrayRaw.length) + ":" + array.length + ":" + arrayRaw.length + ":" + contestSeason.getId());
            batch.add(contestSeason);
            if (batch.size() > 100) {
                logger.error("save batch");
                helloService.saveContestSeasonList(batch);
                batch.clear();
            }
        }
        logger.error("save all begin");
        helloService.saveContestSeasonList(batch);
        logger.error("save all end");
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "修改文章body_raw里的img")
    @RequestMapping(value = "api/tools/article/update/local/img", method = RequestMethod.GET)
    public void updateArticleImage() {
        List<Article> all = articleRepository.findAll(Specification.where(null));
        List<Article> batch = new ArrayList<>();
        for (Article article : all) {
            String bodyRaw = article.getBodyRaw();
            String body = article.getBody();
            String regex = "https://img.shuyiwa.com/article[^\"]*";
            String[] array = findAll(body, regex);

            String regexRaw = "https://frog-console.shuyiwa.com/pass/disk/upload[^\"]*";
            String[] arrayRaw = findAll(bodyRaw, regexRaw);

            if (array.length == arrayRaw.length) {
                for (int i = 0; i < array.length; i++) {
                    bodyRaw = bodyRaw.replace(arrayRaw[i], array[i]);
                }
            }
            article.setBodyRaw(bodyRaw);
            logger.error("article:equal:" + (array.length == arrayRaw.length) + ":" + array.length + ":" + arrayRaw.length + ":" + article.getId());
            batch.add(article);
            if (batch.size() > 100) {
                logger.error("save batch");
                helloService.saveArticles(batch);
                batch.clear();
            }
        }
        logger.error("save all begin");
        helloService.saveArticles(batch);
        logger.error("save all end");
    }

    @Autowired
    HelloService helloService;

    @Autowired
    OrganizationService organizationService;

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "导入机构添加管理员")
    @RequestMapping(value = "api/tools/import/organization/add/admin", method = RequestMethod.GET)
    public void addAdmin() throws FrogException, IOException {
        Map<String, Organization.OrganizationType> otMap = Arrays.stream(Organization.OrganizationType.values()).collect(Collectors.toMap(a -> a.getLabel(), a -> a));
        XSSFWorkbook sheets = new XSSFWorkbook(new FileInputStream(new File("/tmp/艺术培训机构清单-无锡地区导入.xlsx")));
        XSSFSheet sheet = sheets.getSheetAt(0);
        for (int i = 1; i < sheet.getLastRowNum(); i++) {
            XSSFRow row = sheet.getRow(i);
            for (Organization organization : organizationRepository.findByName(getCell(row, 1))) {
                if (new SimpleDateFormat("yyyy-MM-dd HH").format(organization.getCreateTime()).equals("2020-07-07 16")) {
                    organization.setLogo("https://img.shuyiwa.com/static/organization.png");
                    List<OrganizationService.OrganizationAdmin> organizationAdminList = new ArrayList<>();
                    {
                        OrganizationService.OrganizationAdmin organizationAdmin = new OrganizationService.OrganizationAdmin();
                        organizationAdmin.setPhone(getCell(row, 7));
                        organizationAdmin.setSuperAdmin(false);
                        organizationAdminList.add(organizationAdmin);
                    }
                    {
                        OrganizationService.OrganizationAdmin organizationAdmin = new OrganizationService.OrganizationAdmin();
                        organizationAdmin.setPhone("18015320330");
                        organizationAdmin.setSuperAdmin(false);
                        organizationAdminList.add(organizationAdmin);
                    }
                    organizationService.save(organization, organizationAdminList.toArray(new OrganizationService.OrganizationAdmin[organizationAdminList.size()]), null);
                }
            }
        }


    }

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "导入机构")
    @RequestMapping(value = "api/tools/import/organization", method = RequestMethod.GET)
    public void importOrg() throws FrogException, IOException {
        Map<String, Organization.OrganizationType> otMap = Arrays.stream(Organization.OrganizationType.values()).collect(Collectors.toMap(a -> a.getLabel(), a -> a));
        XSSFWorkbook sheets = new XSSFWorkbook(new FileInputStream(new File("/tmp/艺术培训机构清单-无锡地区导入.xlsx")));
        XSSFSheet sheet = sheets.getSheetAt(0);
        for (int i = 1; i < sheet.getLastRowNum(); i++) {
            XSSFRow row = sheet.getRow(i);
            Organization organization = new Organization();
            organization.setName(getCell(row, 1));
            organization.setAddress(getCell(row, 2) + " " + getCell(row, 3) + " " + getCell(row, 4));
            Organization.OrganizationType organizationType = otMap.get(getCell(row, 5));
            if (organizationType == null) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, getCell(row, 5));
            }
            organization.setOrganizationType(organizationType);
            organization.setSummary(getCell(row, 6));
            OrganizationService.OrganizationAdmin organizationAdmin = new OrganizationService.OrganizationAdmin();
            organizationAdmin.setPhone(getCell(row, 7));
            organizationAdmin.setSuperAdmin(false);
            if (organizationRepository.findByName(organization.getName()).size() > 0) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "机构已经存在:" + organization.getName());
            }
            logger.error("save:batch:org:" + organization.getName());
            organizationService.save(organization, new OrganizationService.OrganizationAdmin[]{organizationAdmin}, null);
        }


    }

    private static String getCell(XSSFRow row, int i) {
        XSSFCell cell = row.getCell(i);
        if (cell != null) {
            cell.setCellType(CellType.STRING);
            return cell.getStringCellValue();
        }
        return "";
    }


    public static void main(String[] args) {

        String body = "<html>  <head></head>  <body>   <p><br></p>   <div class=\"media-wrap image-wrap\">    <img src=\"https://img.shuyiwa.com/article/2020/05/15/40288a8b7217abc7017217af0d8d016b.jpg?ts=1589535248126\">   </div>   <p><br></p>   <div class=\"media-wrap image-wrap\">    <img src=\"https://img.shuyiwa.com/article/2020/05/15/40288a8b7217abc7017217af0f02016d.jpg?ts=1589535248249\">   </div>   <p><br></p>   <div class=\"media-wrap image-wrap\">    <img src=\"https://img.shuyiwa.com/article/2020/05/15/40288a8b7217abc7017217af0f7d016f.jpg?ts=1589535248424\">   </div>   <p><br></p>   <div class=\"media-wrap image-wrap\">    <img src=\"https://img.shuyiwa.com/article/2020/05/15/40288a8b7217abc7017217af102b0173.jpg?ts=1589535248547\">   </div>   <p><br></p>   <div class=\"media-wrap image-wrap\">    <img src=\"https://img.shuyiwa.com/article/2020/05/15/40288a8b7217abc7017217af10a70175.jpg?ts=1589535248720\">   </div>   <p><br></p>   <p><br></p>   <p><br></p>   <p><br></p>   <p><br></p>   <p><br></p>   <p><br></p>   <p><br></p>   <p><br></p>  </body> </html>";
        String regex = "https://img.shuyiwa.com/article[^\"]*";
        String[] array = findAll(body, regex);
        Arrays.stream(array).forEach(System.out::println);

        String bodyRaw = "{\"blocks\":[{\"key\":\"9v2fo\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"2047f\",\"text\":\" \",\"type\":\"atomic\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[{\"offset\":0,\"length\":1,\"key\":0}],\"data\":{}},{\"key\":\"c4i92\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"7rc2f\",\"text\":\" \",\"type\":\"atomic\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[{\"offset\":0,\"length\":1,\"key\":1}],\"data\":{}},{\"key\":\"f07l6\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"5jh9t\",\"text\":\" \",\"type\":\"atomic\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[{\"offset\":0,\"length\":1,\"key\":2}],\"data\":{}},{\"key\":\"am8ru\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"fdovh\",\"text\":\" \",\"type\":\"atomic\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[{\"offset\":0,\"length\":1,\"key\":3}],\"data\":{}},{\"key\":\"8g7v8\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"60pq4\",\"text\":\" \",\"type\":\"atomic\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[{\"offset\":0,\"length\":1,\"key\":4}],\"data\":{}},{\"key\":\"76pbf\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"9scef\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"8k7ae\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"a27ja\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"3fpj8\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"3232m\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"9h6lh\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"3e1qn\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}},{\"key\":\"cttd1\",\"text\":\"\",\"type\":\"unstyled\",\"depth\":0,\"inlineStyleRanges\":[],\"entityRanges\":[],\"data\":{}}],\"entityMap\":{\"0\":{\"type\":\"IMAGE\",\"mutability\":\"IMMUTABLE\",\"data\":{\"url\":\"https://frog-console.shuyiwa.com/pass/disk/upload/2020-05-15/40288a8c721615b90172168524c60e0f.jpg\",\"type\":\"IMAGE\"}},\"1\":{\"type\":\"IMAGE\",\"mutability\":\"IMMUTABLE\",\"data\":{\"url\":\"https://frog-console.shuyiwa.com/pass/disk/upload/2020-05-15/40288a8b7217abc7017217ae93590131.jpg\",\"type\":\"IMAGE\"}},\"2\":{\"type\":\"IMAGE\",\"mutability\":\"IMMUTABLE\",\"data\":{\"url\":\"https://frog-console.shuyiwa.com/pass/disk/upload/2020-05-15/40288a8c7217a89b017217aeabd200cd.jpg\",\"type\":\"IMAGE\"}},\"3\":{\"type\":\"IMAGE\",\"mutability\":\"IMMUTABLE\",\"data\":{\"url\":\"https://frog-console.shuyiwa.com/pass/disk/upload/2020-05-15/40288a8d7217aa2c017217aee00d003a.jpg\",\"type\":\"IMAGE\"}},\"4\":{\"type\":\"IMAGE\",\"mutability\":\"IMMUTABLE\",\"data\":{\"url\":\"https://frog-console.shuyiwa.com/pass/disk/upload/2020-05-15/40288a8b7217abc7017217aefc8d0167.jpg\",\"type\":\"IMAGE\"}}}}";
        String regexRaw = "https://frog-console.shuyiwa.com/pass/disk/upload[^\"]*";
        String[] arrayRaw = findAll(bodyRaw, regexRaw);
        Arrays.stream(arrayRaw).forEach(System.out::println);
    }

    private static String[] findAll(String body, String regex) {
        if (body == null) return new String[0];
        Pattern compile = Pattern.compile(regex);
        Matcher matcher = compile.matcher(body);
        List<String> o = new ArrayList<>();
        while (matcher.find()) {
            o.add(matcher.group());
        }
        return o.toArray(new String[o.size()]);
    }

    @Autowired
    LoginUserFileService loginUserFileService;

    @Transactional(rollbackFor = Throwable.class)
    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "修改机构logo")
    @RequestMapping(value = "api/tools/change/organization", method = RequestMethod.GET)
    public void changeOrganizationLog() throws FrogException {
        List<Organization> collect = organizationRepository.findAll((root, query, criteriaBuilder) -> criteriaBuilder.isNotNull(root.get("logo"))).stream().collect(Collectors.toList());
        for (Organization organization : collect) {
            if (!StringUtils.isEmpty(organization.getLogo())) {
                LoginUserFile loginUserFile = loginUserFileService.getFromDiskUrl(organization.getLogo());
                if (loginUserFile != null) {
                    organization.setLogo(loginUserFileService.upload("organization-logo", loginUserFile));
                    organizationRepository.save(organization);
                }
            }
        }

    }


    @Transactional(rollbackFor = Throwable.class)
    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "修改第六届报名信息的年龄分组")
    @RequestMapping(value = "api/tools/contest/age/range/update", method = RequestMethod.POST)
    public int now(
            @RequestParam(value = "contestSeasonId", required = false, defaultValue = Const.diliujie) String contestSeasonId
    ) throws FrogException {
        Specification<ContestantInfo> contestantInfoSpecification = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestSeason").get("id"), contestSeasonId);
        List<ContestantInfo> collect = contestantInfoRepository.findAll(Specification.where(contestantInfoSpecification)).stream().collect(Collectors.toList());
        for (ContestantInfo contestantInfo : collect) {
            contestService.checkAgeRange(contestantInfo);
        }
        contestantInfoRepository.saveAll(collect);
        return collect.size();
    }

//    @Autowired
//    CertificateInstanceRepository certificateInstanceRepository;
//    @Transactional(rollbackFor = Throwable.class)
//    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "测试赛季")
//    @RequestMapping(value = "api/tools/now1", method = RequestMethod.GET)
//    public List<CertificateInstance> nowaa() {
//       return  certificateInstanceRepository.mineForContestSeason("00000000000000000000000000000001","40288a8b69be9e870169bebc314a00e6",PageRequest.of(0,10));
//
//    }

//
//    @Transactional(rollbackFor = Throwable.class)
//    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "将证书改到人身上")
//    @RequestMapping(value = "api/tools/certification/transform", method = RequestMethod.GET)
//    public boolean transformCertification() {
//        certificateInstanceRepository.findAll().forEach(certificateInstance -> {
//            certificateInstance.setWorks(certificateInstance.getWorks());
//            certificateInstanceRepository.save(certificateInstance);
//        });
//        return true;
//    }

    @Transactional(rollbackFor = Throwable.class)
    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "数据库时间")
    @RequestMapping(value = "api/tools/now2", method = RequestMethod.GET)
    public Date cachedNow() {
        return contestScheduleRepository.cachedNow();
    }

//    @Transactional(rollbackFor = Throwable.class)
//    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "测试级链")
//    @RequestMapping(value = "api/tools/get/a", method = RequestMethod.GET)
//    public Iterable<A> aList() {
//        return aRepository.findAll();
//    }
//
//    @Transactional(rollbackFor = Throwable.class)
//    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "测试级链")
//    @RequestMapping(value = "api/tools/save/a", method = RequestMethod.POST)
//    public A bb(@RequestBody A a) {
//        return aRepository.save(a);
//    }


    @Autowired
    WorksService worksService;

    @Transactional(rollbackFor = Throwable.class)
    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "恢复作品投票")
    @RequestMapping(value = "api/tools/update/works/vote", method = RequestMethod.POST)
    public void updaetWorksVote() {
        worksService.updateWorksVote();
    }


    @Transactional(rollbackFor = Throwable.class)
    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "恢复bug投票重的有效票")
    @RequestMapping(value = "api/tools/works/vote/restore/from/bug", method = RequestMethod.POST)
    public void restoreBugVote() {
        worksService.restoreBugVote();
    }

    @Transactional(rollbackFor = Throwable.class)
    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "other to bug")
    @RequestMapping(value = "api/tools/works/vote/other/to/bug", method = RequestMethod.POST)
    public void other2bug() {
        worksService.other2Bug();
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "指定用户指定勋章是否获得")
    @RequestMapping(value = "api/tools/task/check", method = RequestMethod.GET)
    public boolean checkTask(@RequestParam("loginUserId") String loginUserId, @RequestParam("taskId") String taskId) {
        ArrayList<LoginUserTask> userTaskList = new ArrayList<>();
        userTaskList.add(loginUserTaskRepository.findById(taskId).get());
        userTaskService.checkTaskProgress(loginUserRepository.findById(loginUserId).orElse(null), userTaskList);
        return loginUserTaskProgressRepository.findByLoginUser_Id(loginUserId)
                .stream().filter(t -> t.getLoginUserTask().getId().equals(taskId))
                .filter(t -> t.getCompleteTime() != null).findFirst().isPresent();
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "指定用户是否满足task条件")
    @RequestMapping(value = "api/tools/task/check/only", method = RequestMethod.GET)
    public boolean onlyCheck(@RequestParam("loginUserId") String loginUserId, @RequestParam("taskId") String taskId) {
        LoginUser loginUser = loginUserRepository.findById(loginUserId).get();
        Optional<LoginUserTask> loginUserTask = loginUserTaskRepository.findById(taskId);
        UserTaskService.Context context = userTaskService.newTaskContext(loginUser);
        return userTaskService.checkTask(loginUserTask.get(), context);
    }

    @Autowired
    AccountRepository accountRepository;
    @Autowired
    BillRepository billRepository;

    @Autowired
    CertificateInstanceRepository certificateInstanceRepository;

    @Transactional(rollbackFor = Throwable.class)
    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "清空task")
    @RequestMapping(value = "api/tools/task/clear", method = RequestMethod.GET)
    public List<LoginUserTaskProgress> clear() {
        {
            for (String taskId : new String[]{"00000000000000000000000000009003", "00000000000000000000000000009002", "00000000000000000000000000009004", "00000000000000000000000000009005"}) {
                Specification<LoginUserTaskProgress> taskSp = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.equal(root.get("loginUserTask").get("id"), taskId);
                Specification<LoginUserTaskProgress> completeTime = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.isNotNull(root.get("completeTime"));
                List<LoginUserTaskProgress> all = loginUserTaskProgressRepository.findAll(taskSp.and(completeTime));
                for (LoginUserTaskProgress loginUserTaskProgress : all) {
                    Bill bill = loginUserTaskProgress.getBill();
                    Account account = accountRepository.findByLoginUserAndCurrencyType(Optional.ofNullable(loginUserTaskProgress.getLoginUser()), CurrencyType.point).get();
                    if (account.getBalance().longValue() >= bill.getValue().longValue()) {
                        loginUserTaskProgress.setBill(null);
                        loginUserTaskProgress.setCompleteTime(null);
                        loginUserTaskProgressRepository.save(loginUserTaskProgress);
                        account.setBalance(account.getBalance().subtract(new BigDecimal(bill.getValue().longValue())));
                        billRepository.deleteById(bill.getId());
                        accountRepository.save(account);
                    }
                }
            }
        }


        {
            Specification<LoginUserTaskProgress> taskSp = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.equal(root.get("loginUserTask").get("id"), "00000000000000000000000000009001");
            Specification<LoginUserTaskProgress> completeTime = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.isNotNull(root.get("completeTime"));
            List<LoginUserTaskProgress> all = loginUserTaskProgressRepository.findAll(taskSp.and(completeTime));
            for (LoginUserTaskProgress loginUserTaskProgress : all) {
                loginUserTaskProgress.setCompleteTime(null);
                loginUserTaskProgressRepository.save(loginUserTaskProgress);
                Specification<CertificateInstance> loginUserSp = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.equal(root.get("loginUser"), loginUserTaskProgress.getLoginUser());
                Specification<CertificateInstance> certificateSp = (root, criteriaQuery, criteriaBuilder) -> criteriaBuilder.equal(root.get("certificate").get("id"), "40288a8d72205d56017220bd92c40ceb");
                CertificateInstance certificateInstance = certificateInstanceRepository.findAll(loginUserSp.and(certificateSp)).stream().findFirst().get();
                certificateInstanceRepository.delete(certificateInstance);
            }
        }
        return new ArrayList<>();
    }

    @Autowired
    ContestantRepository contestantRepository;
//    @Autowired
//    TempContestantRepository tempContestantRepository;
//    @Autowired
//    TempContestantService tempContestantService;

//    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "所有选手的勋章获取情况")
//    @RequestMapping(value = "api/tools/init0805", method = RequestMethod.GET)
//    public void init0805() {
//        tempContestantRepository.deleteAll();
//
//        List<TempContestant> tempContestantList = new ArrayList<>();
//        Consumer<List<TempContestant>> consumer = (list) -> {
//            if (list.size() > 300) {
//                long now = System.currentTimeMillis();
//                tempContestantService.saveAll(list);
//                logger.info("tempContestantService.saveAll,cost:" + (System.currentTimeMillis() - now));
//                list.clear();
//            }
//        };
//
//
//        contestantInfoRepository.findAll(Specification
//                .where((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false))
//                .and((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestantType"), ContestantType.INDIVIDUAL))
//        ).stream().forEach(contestantInfo -> {
//            TempContestant tempContestant = new TempContestant();
//            tempContestant.setContestInfoId(contestantInfo.getId());
//            tempContestant.setName(contestantInfo.getName());
//            tempContestant.setPhone(Optional.ofNullable(contestantInfo.getAgentPhone()).orElse(contestantInfo.getAgentLoginUser().getPhone()));
//            tempContestant.setAddress(Optional.ofNullable(contestantInfo).map(ContestantInfo::getOrganization).map(Organization::getAddress).orElse(contestantInfo.getAgentAddress()));
//            tempContestant.setItems(contestantRepository.findByContestantInfoAndDeleted(contestantInfo, false).stream().map(Contestant::getContestItem).map(ContestItem::getName).distinct().collect(Collectors.joining(",")));
//            tempContestantList.add(tempContestant);
//            consumer.accept(tempContestantList);
//        });
//
//
//        contestantInfoRepository.findAll(Specification
//                .where((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false))
//                .and((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestantType"), ContestantType.GROUP_MEMBER))
//        ).stream().forEach(contestantInfo -> {
//            TempContestant tempContestant = new TempContestant();
//            tempContestant.setContestInfoId(contestantInfo.getId());
//            tempContestant.setName(contestantInfo.getName());
//            tempContestant.setPhone(contestantInfo.getAgentPhone());
//            Optional<ContestantInfo> parent = Optional.ofNullable(contestantInfo).map(ContestantInfo::getParent);
//            tempContestant.setAddress(parent.map(ContestantInfo::getOrganization).map(Organization::getAddress).orElse(contestantInfo.getAgentAddress()));
//            tempContestant.setPphone(parent.map(ContestantInfo::getAgentPhone).orElse(""));
//            tempContestant.setPname(parent.map(ContestantInfo::getName).orElse(""));
//            tempContestant.setItems(parent.map(p -> contestantRepository.findByContestantInfoAndDeleted(p, false).stream()
//                    .map(Contestant::getContestItem)
//                    .filter(Objects::nonNull)
//                    .map(ContestItem::getName)
//                    .distinct().collect(Collectors.joining(","))).orElse("¬"));
//            tempContestantList.add(tempContestant);
//            consumer.accept(tempContestantList);
//        });
//        tempContestantService.saveAll(tempContestantList);
//    }

//    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "所有选手的勋章获取情况")
//    @RequestMapping(value = "api/tools/run0805", method = RequestMethod.GET)
//    @Transactional
//    public void run0805(int size) {
//        tempContestantService.run(size);
//    }


    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "镇江星蛙棋布是否获得")
    @RequestMapping(value = "api/tools/area/w2", method = RequestMethod.GET)
    public List<Map<String, Object>> areaW2(@RequestParam("area") String area) {

        List<LoginUser> apiUsers = contestantInfoRepository.findAll(Specification
                .where((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false))
                .and((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("agentAddress"), "%" + area + "%"))
                .and((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.isNull(root.get("organization")))
        ).stream().map(contestantInfo -> {
            LoginUser agentLoginUser = contestantInfo.getAgentLoginUser();
            agentLoginUser.setProperty("area", contestantInfo.getAgentAddress());
            agentLoginUser.setProperty("type", "独立用户");
            return agentLoginUser;
        }).collect(Collectors.toList());

        List<LoginUser> orgIndUsers = contestantInfoRepository.findAll(Specification
                .where((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false))
                .and((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.isNotNull(root.get("organization")))
                .and((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestantType"), ContestantType.INDIVIDUAL))
                .and((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("organization").get("address"), "%" + area + "%"))
        ).stream().map(contestantInfo -> {
            LoginUser agentLoginUser = contestantInfo.getAgentLoginUser();
            agentLoginUser.setProperty("area", contestantInfo.getOrganization().getAddress());
            agentLoginUser.setProperty("type", "机构个人");
            agentLoginUser.setProperty("org", contestantInfo.getOrganization().getName());
            return agentLoginUser;
        }).collect(Collectors.toList());

        List<LoginUser> orgMemUsers = contestantInfoRepository.findAll(Specification
                .where((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("deleted"), false))
                .and((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.isNotNull(root.get("organization")))
                .and((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.equal(root.get("contestantType"), ContestantType.GROUP_MEMBER))
                .and((Specification<ContestantInfo>) (root, query, criteriaBuilder) -> criteriaBuilder.like(root.get("parent").get("organization").get("address"), "%" + area + "%"))
        ).stream().map(contestantInfo -> {
            LoginUser agentLoginUser = contestantInfo.getAgentLoginUser();
            agentLoginUser.setProperty("area", contestantInfo.getParent().getOrganization().getAddress());
            agentLoginUser.setProperty("org", contestantInfo.getParent().getOrganization().getName());
            agentLoginUser.setProperty("type", "机构组合成员");
            return agentLoginUser;
        }).collect(Collectors.toList());

        Set<LoginUser> loginUserSet = new HashSet<>();
        loginUserSet.addAll(apiUsers);
        loginUserSet.addAll(orgIndUsers);
        loginUserSet.addAll(orgMemUsers);
        logger.info("loginUserSet:" + loginUserSet.size());
        AtomicInteger a = new AtomicInteger(0);
        AtomicInteger b = new AtomicInteger(0);
        List<String> taskIdList = new ArrayList<>();
        taskIdList.add("00000000000000000000000000007002");
        List<LoginUserTask> userTaskList = taskIdList.stream().map(id -> loginUserTaskRepository.findById(id).get()).collect(Collectors.toList());
        for (LoginUser loginUser : loginUserSet) {
            b.incrementAndGet();
            boolean w2 = loginUserTaskProgressRepository.findByLoginUser_Id(loginUser.getId())
                    .stream().filter(t -> t.getLoginUserTask().getId().equals("00000000000000000000000000007002"))
                    .filter(t -> t.getCompleteTime() != null).findFirst().isPresent();
            loginUser.setProperty("w2", w2);
            if (!w2) {
                logger.info("loginUserSet:" + loginUserSet.size() + ",i:" + b.get() + ",a:" + a.incrementAndGet());
                userTaskService.checkTaskProgress(loginUser, userTaskList);
            }
        }
        List<Map<String, Object>> mapList = loginUserSet.stream().map(loginUser -> {
            Map<String, Object> map = new HashMap();
            map.put("phone", loginUser.getPhone());
            map.put("area", loginUser.getProperties().get("area"));
            map.put("org", loginUser.getProperties().get("org"));
            map.put("type", loginUser.getProperties().get("type"));
            return map;
        }).collect(Collectors.toList());
        return mapList;
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "测试作品在分组中的排名")
    @RequestMapping(value = "api/hello/test/works/rank", method = RequestMethod.GET)
    Number testWorks(String worksId) {
        return worksRepository.findById(worksId).map(works -> {
            return worksRepository.worksRankInItem(works.getId(), works.getContestant().getContestItem().getId(), works.getContestant().getContestantInfo().getContestSeason().getId());
        }).orElse(-1);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "测试通知")
    @RequestMapping(value = "api/hello/test/message", method = RequestMethod.GET)
    void testMessage() {
        messageService.checkMessageTask();
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "测试排名")
    @RequestMapping(value = "api/hello/test/rank", method = RequestMethod.GET)
    void test(@RequestParam int day) {
        withName("loginUserRankInContestSeasonService.check(" + day + ")", () -> loginUserRankInContestSeasonService.check(day), (tag, e) -> logger.warn(tag, e));
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "发送邮件")
    @RequestMapping(value = "api/frog/randk/test", method = RequestMethod.GET)
    List<Map<String, Object>> testa() {
        return contestantInfoRepository.topOrganizationInSeason("00000000000000000000000000000001", PageRequest.of(0, 50));
    }


    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "测试发送邮件")
    @RequestMapping(value = "api/mail/test", method = RequestMethod.GET)
    void test(
            @RequestParam("title") String titile,
            @RequestParam("body") String body
    ) {
        warnService.warn(titile, body);
    }

    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "测试异常")
    @RequestMapping(value = "api/mail/test/e", method = RequestMethod.GET)
    void e(
    ) {
        throw new RuntimeException("测试异常");
    }

//    @RuntimeDoc(client = RuntimeDoc.Client.Tool, desc = "发")
//    @RequestMapping(value = "api/hello/test/vote2", method = RequestMethod.GET)
//    void testVote(
//    ) {
//        contestService.assignVotes();
//    }

    @PreAuthorize("isAuthenticated() && hasAuthority('ADMIN')")
    @RequestMapping("/api/hello/add/org")
    @ResponseBody
    @Transactional
    public void addOrg() throws Exception {
        String a = "南京市鼓楼区少年宫        \t13951637004\n" +
                "南京市玄武区少年宫         \t13951637004\n" +
                "南京市建邺区少年宫         \t13951637004\n" +
                " 南京市江北新区少年宫     \t13951637004\n" +
                "南京市雨花区少年宫        \t13951637004\n" +
                "南京市浦口区青少年宫                  \t13951637004\n" +
                "南京市溧水区青少年活动中心   \t13951637004\n" +
                "南京市高淳区青少年活动中心 \t13951637004\n" +
                "南京市外国语学校\t13951637004\n" +
                "南京市第二十九中初中部\t13951637004\n" +
                "南京市金陵汇文学校初中部\t13951637004\n" +
                "南京市第九中学初中部\t13951637004\n" +
                "南京市宁海中学分校\t13951637004\n" +
                "南师附中新城初级中学\t13951637004\n" +
                "南京市第二十九中致远中学\t13951637004\n" +
                "南京市第五十中学\t13951637004\n" +
                "南京市行知实验中学\t13951637004\n" +
                "南京市琅琊路小学\t13951637004\n" +
                "南京市赤壁路小学\t13951637004\n" +
                "南京市芳草园小学\t13951637004\n" +
                "南京市拉萨路小学\t13951637004\n" +
                "南京市游府西街小学\t13951637004\n" +
                "南京市北京东路小学\t13951637004\n" +
                "南京市致远外国语小学\t13951637004\n" +
                "南京市致远外国语小学乐山分校\t13951637004\n" +
                "南京市汉江路小学\t13951637004\n" +
                "南京市汇文小学\t13951637004\n" +
                "南京市小市中心小学\t13951637004\n" +
                "南京市南大附小\t13951637004\n" +
                "南京市莫愁新寓小学\t13951637004\n" +
                "南京市青云巷小学\t13951637004\n" +
                "南京市马台街小学\t13951637004\n" +
                "南京市科睿小学\t13951637004\n" +
                "南京市南湖第一小学\t13951637004\n" +
                "南京市双塘小学\t13951637004\n" +
                "南京市龙江小学\t13951637004\n" +
                "南京市夫子庙小学\t13951637004\n" +
                "南京市雨花实验小学\t13951637004\n" +
                "南京市莫愁湖小学\t13951637004\n" +
                "南京市江东门小学\t13951637004\n" +
                "南京市锁金二小\t13951637004\n" +
                "南京市栖霞区化纤小学\t13951637004\n" +
                "南京市雨花台区教师发展中心\t13951637004";
        for (String line : a.split("\n")) {
            String[] split = line.split("\t");
            String name = split[0].trim();
            String phone = split[1].trim();

            LoginUser loginUser = loginUserService.createLoginUser(phone,null);
            if (organizationRepository.findByName(name).size() == 0) {
                Organization organization = new Organization();
                organization.setName(name);
                organization.setAddress("江苏 南京 无");
                organization = organizationRepository.save(organization);

                LoginUserAuthority loginUserAuthority = new LoginUserAuthority();
                loginUserAuthority.setLoginUser(loginUser);
                loginUserAuthority.setEntityId(organization.getId());
                loginUserAuthority.setAuthority(Authority.ADMIN_ORGANIZATION);

                loginUserAuthorityRepository.save(loginUserAuthority);
            }
        }
    }


    @PostMapping(value ="/cacheLock2")
    @ResubmitLock(prefix = "redisLock.test",expire = 20)
    public String cacheLock2(@CacheParam(name = "token")@RequestParam(name = "token") String token){
        return "sucess====="+token;
    }

}

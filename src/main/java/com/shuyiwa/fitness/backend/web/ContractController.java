package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.channel.ChannelService;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.*;

import com.shuyiwa.fitness.backend.domain.dict.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.sec.FrogUserDetailsService;
import com.shuyiwa.fitness.backend.service.AppointmentService;
import com.shuyiwa.fitness.backend.service.ContractHistoryService;
import com.shuyiwa.fitness.backend.service.ContractService;
import org.apache.commons.lang3.time.DateFormatUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.apache.poi.hssf.usermodel.*;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.CellStyle;
import org.apache.poi.ss.usermodel.FillPatternType;
import org.apache.poi.ss.usermodel.IndexedColors;
import org.apache.poi.xssf.usermodel.XSSFCellStyle;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;

import org.springframework.web.bind.annotation.*;

import javax.servlet.ServletOutputStream;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.text.DateFormat;
import java.text.ParseException;
import java.util.*;

import static org.apache.poi.ss.util.CellUtil.createCell;

@RestController
public class ContractController {
    private static final Log logger = LogFactory.getLog(ContractController.class);

    @Autowired
    private ContractRepository contractRepository;

    @Autowired
    private CourseRepository courseRepository;

    @Autowired
    private ContractService contractService;

    @Autowired
    private LoginUserRepository loginUserRepository;

    @Autowired
    private AppointmentRepository appointmentRepository;

    @Autowired
    AppointmentService appointmentService;

    @Autowired
    ChannelService channelService;

    @Autowired
    private StoreDataDetailsRepository storeDataDetailsRepository;

    @Autowired
    private ContractHistoryService contractHistoryService;

    @Autowired
    private RecordStoreDataRepository recordStoreDataRepository;

    @PreAuthorize("isAuthenticated()  && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "添加新合同")
    @RequestMapping(value = "api/contract/save", method = RequestMethod.POST)
    public void saveContract(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails, @RequestBody Contract contract
    ) throws FrogException {
        if (StringUtils.isEmpty(contract.getNumberId())) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "合同编号必填");
        }
        if (StringUtils.isEmpty(contract.getOrganization())) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "机构id必填");
        }
        if (StringUtils.isEmpty(contract.getCourseId())) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程必选");
        }
        if (StringUtils.isEmpty(contract.getContractEndTime())) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "合同截止时间必填");
        }
        if (StringUtils.isEmpty(contract.getTotalAmount())) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程金额必填");
        }
        if (StringUtils.isEmpty(contract.getClassHour())) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程数量必填");
        }
        if (StringUtils.isEmpty(contract.getSignatoryId())) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "签约人必填");
        }
        Contract contract1 = contractRepository.findByNumberId(contract.getNumberId());
        if (!StringUtils.isEmpty(contract1)) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "该合同编号已存在");
        }
        contractService.saveContract(frogUserDetails, contract);
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "分页查询合同列表")
    @RequestMapping(value = "api/contract/page", method = RequestMethod.GET)
    public Page<Contract> pageContract(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam int page, @RequestParam int size,
            @RequestParam String organizationId,
            @RequestParam(value = "search", required = false, defaultValue = "") String search,
            @RequestParam(required = false) String status,
            @RequestParam(value = "type",required = false,defaultValue = "1") String type  //1:手机  2：客户   3：签约者  4：课程
    ) throws FrogException {
        Page<Contract> pageResult = contractService.pageContract(organizationId, search, status, page, size,type);
        return pageResult;
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "根据合约id查询约课信息")
    @RequestMapping(value = "api/contract/management", method = RequestMethod.GET)
    public Contract findAppointmentByContractId(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam String organizationId,
            @RequestParam int page, @RequestParam int size,
            @RequestParam String contractId
    ) throws FrogException {
        Contract contract = contractService.findAppointmentByContractId(contractId, page, size);
        return contract;
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "查询单个合同信息")
    @RequestMapping(value = "api/contract/id", method = RequestMethod.GET)
    public Contract findContractById(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam String organizationId,
            @RequestParam String contractId
    ) throws FrogException {
        Contract contract = contractService.findContractById(contractId);
        return contract;
    }

    @Transactional
    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "修改合同信息")
    @RequestMapping(value = "api/contract/update", method = RequestMethod.POST)
    public void updateContract(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestBody Contract contract
    ) throws FrogException {
        Contract contractDB = contractRepository.findById(contract.getId()).orElse(null);
        contract.setUpdateTime(new Date());
        Integer countAppointment = appointmentRepository.countByContract(contract.getId(),contractDB.getOrganization().getId());
        if (countAppointment < 1){
            //未约课(可全部修改)
            if (StringUtils.isEmpty(contract.getNumberId())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "合同编号必填");
            }
            if (StringUtils.isEmpty(contract.getOrganization())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "机构id必填");
            }
            if (StringUtils.isEmpty(contract.getCourseId())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程必选");
            }
            if (StringUtils.isEmpty(contract.getContractEndTime())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "合同截止时间必填");
            }
            if (StringUtils.isEmpty(contract.getTotalAmount())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程金额必填");
            }
            if (StringUtils.isEmpty(contract.getClassHour())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "课程数量必填");
            }
            if (StringUtils.isEmpty(contract.getSignatoryId())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "签约人必填");
            }
            Contract contract2 = contractRepository.findByNumberId(contract.getNumberId());
            if (contract2 != null && !contract2.getId().equals(contract.getId())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "该合同编号已存在");
            }
            if (StringUtils.isEmpty(contract.getFinishClassHour())){
                contract.setFinishClassHour(0);
            }
            if (contract.getClassHour() < contract.getFinishClassHour()){
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "扣减课时数有误");
            }
            contractService.updateContract(contract,contractDB,frogUserDetails);
        } else {
            //已约课(只修改截止时间和签约人)
            if (StringUtils.isEmpty(contract.getContractEndTime())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "合同截止时间必填");
            }
            if (StringUtils.isEmpty(contract.getSignatoryId())) {
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "签约人必填");
            }
            if (StringUtils.isEmpty(contract.getFinishClassHour())){
                contract.setFinishClassHour(0);
            }
            if (contract.getFinishClassHour() != contractDB.getFinishClassHour()){
                if (contractDB.getStatus() != ContractStatus.Contract_NORMAL.getStatus() || contractDB.getRemainingClassHours() == 0){
                    throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "合约已结束或关闭，无法修改扣减课时数");
                }
            }
            if (contractDB.getRemainingClassHours() + contractDB.getFinishClassHour() - contract.getFinishClassHour() < 0){
                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "扣减课时数有误");
            }
//            if (!contract.getFinishClassHour().equals(contractDB.getFinishClassHour())){
//                throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "合同已约课，核销课时数不可修改");
//            }
            //课程异常结束（已到截止时间但还有课）合同修改截止时间后将其status改为正常
            if (contract.getContractEndTime().after(contractDB.getContractEndTime()) && contractDB.getStatus() == ContractStatus.Contract_ABNORMALEND.getStatus()){
                contract.setStatus(ContractStatus.Contract_NORMAL.getStatus());
            } else {
                contract.setStatus(contractDB.getStatus());
            }

            contract.setRemainingClassHours(contractDB.getRemainingClassHours() + contractDB.getFinishClassHour() - contract.getFinishClassHour());
            Integer num = contractRepository.updateContract2(contract.getId(), contract.getContractEndTime(), contract.getUpdateTime(), contract.getSignatoryId(), contract.getStatus(),contract.getRemainingClassHours(),contract.getFinishClassHour(),contractDB.getVersion());
           if(num==0){
               throw new FrogException(FrogException.INTERNAL_SERVER_ERROR,"操作错误，请重试");
           }
//            if (contract.getFinishClassHour() != contractDB.getFinishClassHour()){
//                StoreDataDetails storeDataDetails = new StoreDataDetails();
//                storeDataDetails.setType(StoreDataDetailsStatus.MODIFY_CONTRACT.getStatus());
//                storeDataDetails.setDataId(contractDB.getId());
//                storeDataDetails.setBehavior(StoreDataBehaviorType.modifyContract.name());
//                storeDataDetails.setExecNum(contract.getFinishClassHour()-contractDB.getFinishClassHour());
//                storeDataDetails.setExecAmount(0);
//                storeDataDetails.setRevenueAmount(0);
//                //保存店铺数据详情
//                storeDataDetailsRepository.save(storeDataDetails);
//            }

            Map<String, Object> afterMap = new HashMap<>();
            DateFormat dateFormat = DateFormat.getDateInstance();
            String contractEndTime = dateFormat.format(contract.getContractEndTime());
            String contractEndTimeDB = dateFormat.format(contractDB.getContractEndTime());
            if (!contractEndTime.equals(contractEndTimeDB)) {
                afterMap.put("contractEndTime", contractEndTime);
            }
            if (!contract.getFinishClassHour().equals(contractDB.getFinishClassHour())) {
                afterMap.put("finishClassHour", contract.getFinishClassHour());
            }
            if (!contract.getSignatoryId().equals(contractDB.getSignatoryId())){
                afterMap.put("signatoryId", contract.getSignatoryId());
            }
            if (!afterMap.isEmpty()){
                contractHistoryService.save(contractDB, afterMap, frogUserDetails.getLoginUser(loginUserRepository));
            }
        }
    }

    @Transactional
    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "关闭合同")
    @RequestMapping(value = "api/contract/close", method = RequestMethod.GET)
    public void closeContract(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam String contractId,
            @RequestParam Integer refundAmount
    ) throws FrogException {
        Contract contract = contractRepository.findById(contractId).get();
        Integer status = contract.getStatus();
        if (status.equals(ContractStatus.Contract_NOTCONSUMED.getStatus()) || status.equals(ContractStatus.Contract_CONSUMED.getStatus()) || status.equals(ContractStatus.Contract_NORMALEND.getStatus())) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "此合约已关闭");
        }
        //查看课时是否被消耗过
        if (contract.getClassHour().equals(contract.getRemainingClassHours())) {
            status = ContractStatus.Contract_NOTCONSUMED.getStatus();
        } else {
            status = ContractStatus.Contract_CONSUMED.getStatus();
        }
        String numberIdPre = contract.getNumberId();
        Random random = new Random();
        int num = 0;
        String str = "abcdefghijklmnopqrstuvwxyz";
        num = random.nextInt(26);
        char c = str.charAt(num);
        String numberId = numberIdPre + "[已关闭-" + c + "]";
        Integer count = contractRepository.countByNumberId(numberId);
        int n = 0;
        while(count > 0 || n > 5){
            num = random.nextInt(26);
            c = str.charAt(num);
            numberId = numberIdPre + "[已关闭-" + c + "]";
            count = contractRepository.countByNumberId(numberId);
            n++;
        }
        if (n == 5){
            numberId = numberIdPre + "[已关闭-" + c + "1]";
        }
        contractRepository.closeContract(contractId, numberId, status, refundAmount);

        StoreDataDetails storeDataDetails = new StoreDataDetails();
        storeDataDetails.setType(StoreDataDetailsStatus.REFUND.getStatus());
        storeDataDetails.setDataId(contractId);
        storeDataDetails.setBehavior(StoreDataBehaviorType.refund.name());
        storeDataDetails.setExecNum(contract.getClassHour());
        storeDataDetails.setExecAmount(-refundAmount);
        storeDataDetails.setRevenueAmount(-refundAmount);
        storeDataDetails.setCoachIds(contract.getSignatoryId());
        RecordStoreData recordStoreData = new RecordStoreData();
        recordStoreData.setField(3);
        recordStoreData.setContractId(contractId);
        recordStoreData.setDeleted(false);
        recordStoreData.setCreateTime(new Date());
        recordStoreDataRepository.save(recordStoreData);
        //保存店铺数据详情
        storeDataDetailsRepository.save(storeDataDetails);
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "查询有效合约且开启的课程")
    @RequestMapping(value = "api/contract/ValidContract", method = RequestMethod.GET)
    public List<Contract> findValidContract(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam String organizationId,
            @RequestParam String userId
    ) throws FrogException {
        List<Contract> contractList = contractService.findValidContract(userId, organizationId);
        return contractList;
    }

    @PreAuthorize("isAuthenticated()  && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "导出单个合约及合约约课记录")
    @RequestMapping(value = "api/fitness/contract/appointment/export", method = RequestMethod.GET)
    public void export(@AuthenticationPrincipal FrogUserDetails frogUserDetails,
                       @RequestParam String organizationId,
                       @RequestParam String contractId,
                       HttpServletRequest request, HttpServletResponse response) throws FrogException {
//        Boolean isAdmin = false;
//        isAdmin = frogUserDetails.getAuthorities().stream()
//                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
//                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
//                .map(a -> a.getAuthorityEnum())
//                .filter(authority -> authority == Authority.ADMIN_ORGANIZATION)
//                .count() > 0;
//        String loginUserId = frogUserDetails.getLoginUserId();
        try {
            Contract contract = contractService.findAppointmentByContractId(contractId, 0, 1000);
            Map<String, Object> map = contract.getProperties();
            //Map<String, Page<Appointment>> appointmentMap = (Map<String, Page<Appointment>>) map.get("appointmentMap");
            Page<Appointment> appointmentPage = (Page<Appointment>) map.get("appointmentPage");
            List<Appointment> dataList = appointmentPage.getContent();
            String fileName = "用户%s合约信息.xls";
            LoginUser loginUser1 = frogUserDetails.getLoginUser(loginUserRepository);
            if (!StringUtils.isEmpty(loginUser1)) {
                LoginUser user = loginUserRepository.findById(contract.getUser().getId()).orElseThrow(() -> new FrogException(FrogException.INTERNAL_SERVER_ERROR, "用户不存在"));
                fileName = String.format(fileName, user.getName());
            }
            channelService.setResponseFileName(request, response, fileName);
            HSSFWorkbook workbook = new HSSFWorkbook();
            HSSFSheet sheet = workbook.createSheet("用户信息");
            HSSFRow row = sheet.createRow(0);
            String[] headers1 = {"用户名", "手机号", "签约人", "课程", "合同", "建档", "截止", "金额", "课时", "余课","教练核销课时数", "余额结算"};
            for (int i = 0; i < headers1.length; i++) {
                HSSFCell cell = row.createCell(i);
                HSSFRichTextString text = new HSSFRichTextString(headers1[i]);
                HSSFCellStyle cellStyle = workbook.createCellStyle();
                cellStyle.setFillForegroundColor(IndexedColors.LIGHT_GREEN.getIndex());
                cellStyle.setFillPattern(FillPatternType.SOLID_FOREGROUND);
                cell.setCellStyle(cellStyle);
                cell.setCellValue(text);
            }

            row = sheet.createRow(1);
            createCell(row, 0, contract.getUser().getName());
            createCell(row, 1, contract.getUser().getPhone());
            String[] signatoryIds = contract.getSignatoryId().split(",");
            String signatoryNames = "";
            int length = signatoryIds.length;
            for (String signatoryId : signatoryIds) {
                LoginUser loginUser = loginUserRepository.findById(signatoryId).orElse(null);
                if (!StringUtils.isEmpty(loginUser)) {
                    length--;
                    if (length > 0){
                        signatoryNames = signatoryNames + loginUser.getName() + "、";
                    } else {
                        signatoryNames = signatoryNames + loginUser.getName();
                    }
                }
            }
            createCell(row, 2, signatoryNames);
            Course course = courseRepository.findById(contract.getCourseId()).orElse(null);
            if (!StringUtils.isEmpty(course)) {
                createCell(row, 3, course.getName());
            }

            createCell(row, 4, contract.getNumberId());
            String contractCreateDate = DateFormatUtils.format(contract.getContractCreateTime(), "yyyy-MM-dd");
            createCell(row, 5, contractCreateDate);
            String contractEndDate = DateFormatUtils.format(contract.getContractEndTime(), "yyyy-MM-dd");
            createCell(row, 6, contractEndDate);
            Double totalAmount = contract.getTotalAmount() * 1.0;
            createCell(row, 7, totalAmount.toString());
            createCell(row, 8, contract.getClassHour().toString());
            createCell(row, 9, contract.getRemainingClassHours().toString());
            Integer finishClassHour = StringUtils.isEmpty(contract.getFinishClassHour()) ? 0 : contract.getFinishClassHour();
            createCell(row, 10, String.valueOf(finishClassHour));
            Double remain = totalAmount / contract.getClassHour() * contract.getRemainingClassHours();
            BigDecimal bg = new BigDecimal(remain).setScale(1, RoundingMode.HALF_UP);
            createCell(row, 11, bg.toString());

            HSSFSheet sheet1 = workbook.createSheet("合约信息");
            HSSFRow row1 = sheet1.createRow(0);
            //HSSFRow row1 = sheet.getRow(0);
            String[] headers = {"日期", "时间", "上课教练", "余课结算", "状态"};
            int i1 = 0;
            for (int i = 0; i < headers.length; i++, i1++) {
                HSSFCell cell = row1.createCell(i1);
                HSSFRichTextString text = new HSSFRichTextString(headers[i]);
                HSSFCellStyle cellStyle = workbook.createCellStyle();
                cellStyle.setFillForegroundColor(IndexedColors.LIGHT_GREEN.getIndex());
                cellStyle.setFillPattern(FillPatternType.SOLID_FOREGROUND);
                cell.setCellStyle(cellStyle);
                cell.setCellValue(text);
            }
            for (int i = 0; i < dataList.size(); i++) {
                row = sheet1.createRow(i + 1);
                Appointment appointment = dataList.get(i);
                String appointDate = StringUtils.isEmpty(appointment.getCourseStartTime()) ? "" : DateFormatUtils.format(appointment.getCourseStartTime(), "yyyy-MM-dd");
                createCell(row, 0, appointDate);
                String appointDateStart =  StringUtils.isEmpty(appointment.getCourseStartTime()) ? "" : DateFormatUtils.format(appointment.getCourseStartTime(), "HH:mm");
                createCell(row, 1, appointDateStart);
                createCell(row, 2, appointment.getCoach().getName());
                createCell(row, 3, appointment.getAmount() == null ? "" : appointment.getAmount());//余课结算
                if(appointment.isDeleted()){
                    //被删除了表示取消预约
                    createCell(row, 4, "取消预约");
                } else {
                    createCell(row, 4, AppointmentStatus.getAppointmentName(appointment.getStatus()));
                }
//                String updateDate = StringUtils.isEmpty(appointment.getLastUpdateTime()) ? "" : DateFormatUtils.format(appointment.getLastUpdateTime(), "yyyy-MM-dd");
//                createCell(row, 5, updateDate);
//                String updateDateStart = StringUtils.isEmpty(appointment.getLastUpdateTime()) ? "" : DateFormatUtils.format(appointment.getLastUpdateTime(), "HH:mm");
//                createCell(row, 6, updateDateStart);
            }
            try (ServletOutputStream outputStream = response.getOutputStream()) {
                workbook.write(outputStream);
            }
        } catch (IOException e) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "导出出错");
        }
    }

    @PreAuthorize("isAuthenticated()  && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "合约列表导出")
    @RequestMapping(value = "api/fitness/contract/export", method = RequestMethod.GET)
    public void export(@RequestParam(value = "organizationId") String organizationId,
                       @RequestParam(value = "search", required = false, defaultValue = "") String search,
                       @RequestParam(required = false) String status,
                       @AuthenticationPrincipal FrogUserDetails frogUserDetails,
                       @RequestParam(value = "type",required = false,defaultValue = "1") String type,  //1:手机  2：客户   3：签约者  4：课程
                       HttpServletRequest request, HttpServletResponse response) throws FrogException {
//        Boolean isAdmin = false;
//        isAdmin = frogUserDetails.getAuthorities().stream()
//                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
//                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
//                .map(a -> a.getAuthorityEnum())
//                .filter(authority -> authority == Authority.ADMIN_ORGANIZATION)
//                .count() > 0;
//        String loginUserId = frogUserDetails.getLoginUserId();
        try {
            Page<Contract> contracts = contractService.pageContract(organizationId, search, status, 0, 10000,type);
            String fileName = "合约列表.xls";
            channelService.setResponseFileName(request, response, fileName);
            HSSFWorkbook workbook = new HSSFWorkbook();
            HSSFSheet sheet = workbook.createSheet("合约列表");
            String[] headers = {"编号", "建立日期", "有效期", "签约手机", "签约客户名", "签约人", "课程", "总金额", "标价", "实价", "课时", "余课","教练核销课时数"};

            HSSFRow row = sheet.createRow(0);
            for (int i = 0; i < headers.length; i++) {
                HSSFCell cell = row.createCell(i);
                HSSFRichTextString text = new HSSFRichTextString(headers[i]);
                HSSFCellStyle cellStyle = workbook.createCellStyle();
                cellStyle.setFillForegroundColor(IndexedColors.LIGHT_GREEN.getIndex());
                cellStyle.setFillPattern(FillPatternType.SOLID_FOREGROUND);
                cell.setCellStyle(cellStyle);
                cell.setCellValue(text);
            }
            List<Contract> dataList = contracts.getContent();

            for (int i = 0; i < dataList.size(); i++) {
                row = sheet.createRow(i + 1);
                Contract contract = dataList.get(i);
                createCell(row, 0, contract.getNumberId());
                String createDate = StringUtils.isEmpty(contract.getCreateTime()) ? "" : DateFormatUtils.format(contract.getCreateTime(), "yyyy-MM-dd HH:mm");
                createCell(row, 1, createDate);
                String contractDate = StringUtils.isEmpty(contract.getContractEndTime()) ? "" : DateFormatUtils.format(contract.getContractEndTime(), "yyyy-MM-dd");
                createCell(row, 2, contractDate);
                createCell(row, 3, contract.getUser().getPhone());
                createCell(row, 4, contract.getUser().getName());
                if (!StringUtils.isEmpty(contract.getSignatoryId())) {
                    String[] signatorys = contract.getSignatoryId().split(",");
                    String signatoryName = "";
                    int length = signatorys.length;
                    for (String signatory : signatorys) {
                        LoginUser loginUser = loginUserRepository.findById(signatory).orElse(null);
                        if (!StringUtils.isEmpty(loginUser)) {
                            length--;
                            if (length > 0){
                                signatoryName = signatoryName + loginUser.getName() + "、";
                            } else {
                                signatoryName = signatoryName + loginUser.getName();
                            }
                        }
                    }
                    createCell(row, 5, signatoryName);
                }
                if (!StringUtils.isEmpty(contract.getCourseId())) {
                    Course course = courseRepository.findById(contract.getCourseId()).orElse(null);
                    if (!StringUtils.isEmpty(course)) {
                        createCell(row, 6, course.getName());
                    }
                }
                Double totalAmount = contract.getTotalAmount() * 1.0;
                createCell(row, 7, totalAmount.toString());
                Double coursePrice = courseRepository.findById(contract.getCourseId()).get().getCoursePrice() * 1.0;
                createCell(row, 8, coursePrice.toString());
                Double realPrice = totalAmount / contract.getClassHour();
                BigDecimal bg = new BigDecimal(realPrice).setScale(1, RoundingMode.HALF_UP);
                createCell(row, 9, bg.toString());
                createCell(row, 10, contract.getClassHour().toString());
                createCell(row, 11, contract.getRemainingClassHours().toString());
                Integer finishClassHour = StringUtils.isEmpty(contract.getFinishClassHour()) ? 0 : contract.getFinishClassHour();
                createCell(row, 12, String.valueOf(finishClassHour));
            }
            try (ServletOutputStream outputStream = response.getOutputStream()) {
                workbook.write(outputStream);
            }
        } catch (IOException e) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "导出出错");
        }
    }

    /**
     * find_in_set 的使用
     *
     * @param signid
     */
    @GetMapping("/api/contract/sign/{signid}")
    public void findBySignId(@PathVariable("signid") String signid) {
        System.out.println(contractRepository.findBySignId(signid));
        System.out.println(contractRepository.findBySignIdSql(signid));
    }

    @RuntimeDoc(client = {RuntimeDoc.Client.Api, RuntimeDoc.Client.Console}, desc = "根据用户id查询合同")
    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/contract/userId", method = RequestMethod.GET)
    public List<Contract> findContractByUserId(
            @RequestParam(value = "organizationId") String organizationId,
            @RequestParam(value = "userId", required = false, defaultValue = "") String userId,
            @RequestParam(value = "status", required = false) Integer status,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails
    ) throws FrogException {
        List<Contract> contractList = contractService.findContractByUserId(organizationId,userId,status);
        return contractList;
    }

    @PreAuthorize("isAuthenticated() && (hasAuthority('ADMIN_ORGANIZATION'))")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "合同修改历史数据")
    @RequestMapping(value = "api/contract/contractHistory", method = RequestMethod.GET)
    List<ContractHistory>  findByContractId(@RequestParam(name = "contractId")String contractId)throws FrogException{
        return contractHistoryService.findByContractId(contractId);
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "分页查询合同列表")
    @RequestMapping(value = "api/contract/page1", method = RequestMethod.GET)
    public Page<Contract> pageContract1(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam int page, @RequestParam int size,
            @RequestParam String organizationId,
            @RequestParam(value = "search", required = false, defaultValue = "") String search,
            @RequestParam(required = false) String status
    ) throws FrogException {
        Page<Contract> pageResult = contractService.pageContract(organizationId, search, status, page, size);
        return pageResult;
    }

    @PreAuthorize("isAuthenticated()  && hasAuthority('ADMIN_ORGANIZATION')")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "合约列表导出")
    @RequestMapping(value = "api/fitness/contract/export1", method = RequestMethod.GET)
    public void export1(@RequestParam(value = "organizationId") String organizationId,
                       @RequestParam(value = "search", required = false, defaultValue = "") String search,
                       @RequestParam(required = false) String status,
                       @AuthenticationPrincipal FrogUserDetails frogUserDetails,
                       HttpServletRequest request, HttpServletResponse response) throws FrogException {
//        Boolean isAdmin = false;
//        isAdmin = frogUserDetails.getAuthorities().stream()
//                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
//                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
//                .map(a -> a.getAuthorityEnum())
//                .filter(authority -> authority == Authority.ADMIN_ORGANIZATION)
//                .count() > 0;
//        String loginUserId = frogUserDetails.getLoginUserId();
        try {
            Page<Contract> contracts = contractService.pageContract(organizationId, search, status, 0, 10000);
            String fileName = "合约列表.xls";
            channelService.setResponseFileName(request, response, fileName);
            HSSFWorkbook workbook = new HSSFWorkbook();
            HSSFSheet sheet = workbook.createSheet("合约列表");
            String[] headers = {"编号", "建立日期", "有效期", "签约手机", "签约客户名", "签约人", "课程", "总金额", "标价", "实价", "课时", "余课","教练核销课时数"};

            HSSFRow row = sheet.createRow(0);
            for (int i = 0; i < headers.length; i++) {
                HSSFCell cell = row.createCell(i);
                HSSFRichTextString text = new HSSFRichTextString(headers[i]);
                HSSFCellStyle cellStyle = workbook.createCellStyle();
                cellStyle.setFillForegroundColor(IndexedColors.LIGHT_GREEN.getIndex());
                cellStyle.setFillPattern(FillPatternType.SOLID_FOREGROUND);
                cell.setCellStyle(cellStyle);
                cell.setCellValue(text);
            }
            List<Contract> dataList = contracts.getContent();

            for (int i = 0; i < dataList.size(); i++) {
                row = sheet.createRow(i + 1);
                Contract contract = dataList.get(i);
                createCell(row, 0, contract.getNumberId());
                String createDate = StringUtils.isEmpty(contract.getCreateTime()) ? "" : DateFormatUtils.format(contract.getCreateTime(), "yyyy-MM-dd HH:mm");
                createCell(row, 1, createDate);
                String contractDate = StringUtils.isEmpty(contract.getContractEndTime()) ? "" : DateFormatUtils.format(contract.getContractEndTime(), "yyyy-MM-dd");
                createCell(row, 2, contractDate);
                createCell(row, 3, contract.getUser().getPhone());
                createCell(row, 4, contract.getUser().getName());
                if (!StringUtils.isEmpty(contract.getSignatoryId())) {
                    String[] signatorys = contract.getSignatoryId().split(",");
                    String signatoryName = "";
                    int length = signatorys.length;
                    for (String signatory : signatorys) {
                        LoginUser loginUser = loginUserRepository.findById(signatory).orElse(null);
                        if (!StringUtils.isEmpty(loginUser)) {
                            length--;
                            if (length > 0){
                                signatoryName = signatoryName + loginUser.getName() + "、";
                            } else {
                                signatoryName = signatoryName + loginUser.getName();
                            }
                        }
                    }
                    createCell(row, 5, signatoryName);
                }

                if (!StringUtils.isEmpty(contract.getCourseId())) {
                    Course course = courseRepository.findById(contract.getCourseId()).orElse(null);
                    if (!StringUtils.isEmpty(course)) {
                        createCell(row, 6, course.getName());
                    }
                }
                Double totalAmount = contract.getTotalAmount() * 1.0;
                createCell(row, 7, totalAmount.toString());
                Double coursePrice = courseRepository.findById(contract.getCourseId()).get().getCoursePrice() * 1.0;
                createCell(row, 8, coursePrice.toString());
                Double realPrice = totalAmount / contract.getClassHour();
                BigDecimal bg = new BigDecimal(realPrice).setScale(1, RoundingMode.HALF_UP);
                createCell(row, 9, bg.toString());
                createCell(row, 10, contract.getClassHour().toString());
                createCell(row, 11, contract.getRemainingClassHours().toString());
                Integer finishClassHour = StringUtils.isEmpty(contract.getFinishClassHour()) ? 0 : contract.getFinishClassHour();
                createCell(row, 12, String.valueOf(finishClassHour));
            }
            try (ServletOutputStream outputStream = response.getOutputStream()) {
                workbook.write(outputStream);
            }
        } catch (IOException e) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "导出出错");
        }
    }

    public static void main(String[] args) {
        Scanner scanner = new Scanner(System.in);
        System.out.println("请输入一串长度不超过20的数字：");
        String input = scanner.next();
        if (input.length() > 20) {
            System.out.println("输入的数字串长度超过20，请重新输入！");
            return;
        }

        // 调用处理方法
        List<String> result = decodeWays(input);

        // 输出结果
        System.out.println("所有可能的译码结果为：" + result);

    }

    public static List<String> decodeWays(String digits) {
        List<String> result = new ArrayList<>();

        // 特殊情况处理
        if (digits == null || digits.length() == 0) {
            return result;
        }

        // 初始化动态规划数组
        int n = digits.length();
        int[] dp = new int[n + 1];
        dp[0] = 1;

        // 动态规划计算
        for (int i = 0; i < n; i++) {
            // 单独译码
            if (digits.charAt(i) != '0') {
                dp[i + 1] += dp[i];
            }
            // 判断是否可以和前一个数字组合译码
            if (i > 0 && (digits.charAt(i - 1) == '1' || (digits.charAt(i - 1) == '2' && digits.charAt(i) <= '6'))) {
                dp[i + 1] += dp[i - 1];
            }
        }

        // 回溯求解所有可能的译码结果
        backtrack(digits, "", dp, result, n);

        return result;
    }

    private static void backtrack(String digits, String current, int[] dp, List<String> result, int index) {
        if (index == 0) {
            result.add(current);
            return;
        }

        // 单独译码
        if (digits.charAt(index - 1) != '0') {
            backtrack(digits, (char)('a' + digits.charAt(index - 1) - '1') + current, dp, result, index - 1);
        }

        // 和前一个数字组合译码
        if (index > 1 && (digits.charAt(index - 2) == '1' || (digits.charAt(index - 2) == '2' && digits.charAt(index - 1) <= '6'))) {
            backtrack(digits, (char)('a' + Integer.parseInt(digits.substring(index - 2, index)) - 1) + current, dp, result, index - 2);
        }
    }



















}

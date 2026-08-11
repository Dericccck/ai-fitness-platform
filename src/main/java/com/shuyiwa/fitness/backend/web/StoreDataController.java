package com.shuyiwa.fitness.backend.web;

import com.shuyiwa.fitness.backend.channel.ChannelService;
import com.shuyiwa.fitness.backend.conf.doc.RuntimeDoc;
import com.shuyiwa.fitness.backend.domain.StoreDataDetails;
import com.shuyiwa.fitness.backend.domain.dict.StoreDataBehaviorType;
import com.shuyiwa.fitness.backend.domain.dict.StoreDataType;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.service.StoreDataService;
import org.apache.commons.lang3.time.DateFormatUtils;
import org.apache.poi.hssf.usermodel.*;
import org.apache.poi.ss.usermodel.FillPatternType;
import org.apache.poi.ss.usermodel.IndexedColors;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import javax.servlet.ServletOutputStream;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.text.ParseException;
import java.util.List;
import java.util.Map;

import static org.apache.poi.ss.util.CellUtil.createCell;

@RestController
public class StoreDataController {

    @Autowired
    StoreDataService storeDataService;

    @Autowired
    ChannelService channelService;

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "上课率环形图")
    @RequestMapping(value = "api/fitness/storeData/chart/classRate", method = RequestMethod.GET)
    Map<String, Object> findClassRate(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "startDate") String startDate,
            @RequestParam(value = "endDate") String endDate
    ) throws ParseException {
        Map<String, Object> resultList = storeDataService.findClassRate(startDate, endDate);
        return resultList;
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "营收折线图")
    @RequestMapping(value = "api/fitness/storeData/chart/revenue", method = RequestMethod.GET)
    List<Map<String, Object>> findRevenue(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "startDate") String startDate,
            @RequestParam(value = "endDate") String endDate
    ) throws ParseException {
        List<Map<String, Object>> resultList = storeDataService.findStoreData(startDate, endDate, StoreDataType.revenueAmount.name());
        return resultList;
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "新客折线图")
    @RequestMapping(value = "api/fitness/storeData/chart/newCustomer", method = RequestMethod.GET)
    List<Map<String, Object>> findNewCustomer(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "startDate") String startDate,
            @RequestParam(value = "endDate") String endDate
    ) throws ParseException {
        List<Map<String, Object>> resultList = storeDataService.findStoreData(startDate, endDate, StoreDataType.newCustomer.name());
        return resultList;
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "完成课程折线图")
    @RequestMapping(value = "api/fitness/storeData/chart/finishAppointment", method = RequestMethod.GET)
    List<Map<String, Object>> findFinishAppointment(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "startDate") String startDate,
            @RequestParam(value = "endDate") String endDate
    ) throws ParseException {
        List<Map<String, Object>> resultList = storeDataService.findStoreData(startDate, endDate, StoreDataType.finishAppointment.name());
        return resultList;
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "购买课程折线图")
    @RequestMapping(value = "api/fitness/storeData/chart/classHour", method = RequestMethod.GET)
    List<Map<String, Object>> findClassHour(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "startDate") String startDate,
            @RequestParam(value = "endDate") String endDate
    ) throws ParseException {
        List<Map<String, Object>> resultList = storeDataService.findStoreData(startDate, endDate, StoreDataType.classHour.name());
        return resultList;
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "课程购买金额折线图")
    @RequestMapping(value = "api/fitness/storeData/chart/totalAmount", method = RequestMethod.GET)
    List<Map<String, Object>> findTotalAmount(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "startDate") String startDate,
            @RequestParam(value = "endDate") String endDate
    ) throws ParseException {
        List<Map<String, Object>> resultList = storeDataService.findStoreData(startDate, endDate, StoreDataType.totalAmount.name());
        return resultList;
    }


    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "店铺数据详情分页")
    @RequestMapping(value = "api/fitness/storeDataDetails", method = RequestMethod.GET)
    Page<StoreDataDetails> findStoreDataDetails(
            @RequestParam(value = "organizationId") String organizationId,
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "startDate", required = true) String startDate,
            @RequestParam(value = "endDate", required = true) String endDate,
            @RequestParam(value = "coachId", required = false) String coachId,
            @RequestParam(value = "type", required = false) String type,
            @RequestParam int page, @RequestParam int size
    ) throws ParseException {
        Page<StoreDataDetails> resultList = storeDataService.findStoreDataDetails(startDate, endDate, coachId, type, page, size);
        return resultList;
    }

    @PreAuthorize("isAuthenticated()")
    @RequestMapping(value = "api/fitness/storeDataDetails/export", method = RequestMethod.GET)
    public void storeDataDetailsExport(@RequestParam(value = "organizationId") String organizationId,
                                       @AuthenticationPrincipal FrogUserDetails frogUserDetails,
                                       @RequestParam(value = "startDate") String startDate,
                                       @RequestParam(value = "endDate") String endDate,
                                       @RequestParam(value = "coachId", required = false) String coachId,
                                       @RequestParam(value = "type", required = false) String type,
                                       HttpServletRequest request, HttpServletResponse response) throws FrogException {
        try {
            Page<StoreDataDetails> pages = storeDataService.findStoreDataDetails(startDate, endDate, coachId, type, 0, 1000);
            String fileName = "店铺数据详情表.xls";
            channelService.setResponseFileName(request, response, fileName);
            HSSFWorkbook workbook = new HSSFWorkbook();
            HSSFSheet sheet = workbook.createSheet("数据详情");
            String[] headers = {"日期", "时间", "教练/销售", "客户", "行为", "类型", "合约编号", "合约名称", "执行数量", "执行金额", "营收结算"};
            HSSFRow row = sheet.createRow(1);
            for (int i = 0; i < headers.length; i++) {
                HSSFCell cell = row.createCell(i);
                HSSFRichTextString text = new HSSFRichTextString(headers[i]);
                HSSFCellStyle cellStyle = workbook.createCellStyle();
                if (i < 9) {
                    cellStyle.setFillForegroundColor(IndexedColors.LIGHT_TURQUOISE.getIndex());
                    cellStyle.setFillPattern(FillPatternType.SOLID_FOREGROUND);
                } else {
                    cellStyle.setFillForegroundColor(IndexedColors.PALE_BLUE.getIndex());
                    cellStyle.setFillPattern(FillPatternType.SOLID_FOREGROUND);
                }
                cell.setCellStyle(cellStyle);
                cell.setCellValue(text);
            }
            Integer totalRevenueAmount = 0;
            List<StoreDataDetails> dataList = pages.getContent();
            for (int i = 0; i < dataList.size(); i++) {
                row = sheet.createRow(i + 2);
                StoreDataDetails storeDataDetails = dataList.get(i);
                totalRevenueAmount += storeDataDetails.getRevenueAmount();
                String dateDate = StringUtils.isEmpty(storeDataDetails.getCreateTime()) ? "" : DateFormatUtils.format(storeDataDetails.getCreateTime(), "yyyy-MM-dd");
                createCell(row, 0, dateDate);
                String dateTime = StringUtils.isEmpty(storeDataDetails.getCreateTime()) ? "" : DateFormatUtils.format(storeDataDetails.getCreateTime(), "HH:mm");
                createCell(row, 1, dateTime);
                createCell(row, 2, StringUtils.isEmpty(storeDataDetails.getProperties().get("coachName")) ? "" : storeDataDetails.getProperties().get("coachName").toString());
                createCell(row, 3, StringUtils.isEmpty(storeDataDetails.getProperties().get("userName")) ? "" : storeDataDetails.getProperties().get("userName").toString());
                String behavior = storeDataDetails.getBehavior();
                String behaviorName = "";
                if (StoreDataBehaviorType.finishAppointment.name().equals(behavior)) {
                    behaviorName = "完成课程";
                } else if (StoreDataBehaviorType.refund.name().equals(behavior)) {
                    behaviorName = "退款";
                } else if (StoreDataBehaviorType.buyCourse.name().equals(behavior)) {
                    behaviorName = "购买";
                } else if (StoreDataBehaviorType.modifyContract.name().equals(behavior)) {
                    behaviorName = "修改合约";
                }
                createCell(row, 4, behaviorName);
                Integer type1 = storeDataDetails.getType();
                String typeName = "";
                switch (type1) {
                    case 1:
                        typeName = "新客";
                        break;
                    case 2:
                        typeName = "改";
                        break;
                    case 3:
                        typeName = "退";
                        break;
                    case 4:
                        typeName = "";
                        break;
                }
                createCell(row, 5, typeName);
                createCell(row, 6, StringUtils.isEmpty(storeDataDetails.getProperties().get("numberId")) ? "" : storeDataDetails.getProperties().get("numberId").toString());
                createCell(row, 7, StringUtils.isEmpty(storeDataDetails.getProperties().get("courseName")) ? "" : storeDataDetails.getProperties().get("courseName").toString());
                createCell(row, 8, StringUtils.isEmpty(storeDataDetails.getExecNum()) ? "0" : storeDataDetails.getExecNum().toString());
                createCell(row, 9, StringUtils.isEmpty(storeDataDetails.getExecAmount()) ? "0" : storeDataDetails.getExecAmount().toString());
                createCell(row, 10, StringUtils.isEmpty(storeDataDetails.getRevenueAmount()) ? "0" : storeDataDetails.getRevenueAmount().toString());
            }
            HSSFRow row0 = sheet.createRow(0);
            HSSFCellStyle cellStyle1 = workbook.createCellStyle();
            cellStyle1.setFillForegroundColor(IndexedColors.GREY_50_PERCENT.getIndex());
            cellStyle1.setFillPattern(FillPatternType.SOLID_FOREGROUND);
            createCell(row0, 9, "总计").setCellStyle(cellStyle1);
            HSSFCellStyle cellStyle2 = workbook.createCellStyle();
            cellStyle2.setFillForegroundColor(IndexedColors.GREY_25_PERCENT.getIndex());
            cellStyle2.setFillPattern(FillPatternType.SOLID_FOREGROUND);
            createCell(row0, 10, totalRevenueAmount.toString()).setCellStyle(cellStyle2);
            try (ServletOutputStream outputStream = response.getOutputStream()) {
                workbook.write(outputStream);
            }
        } catch (ParseException e) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "数据出错");
        } catch (IOException e) {
            throw new FrogException(FrogException.INTERNAL_SERVER_ERROR, "导出出错");
        }
    }

    @PreAuthorize("isAuthenticated()")
    @RuntimeDoc(client = RuntimeDoc.Client.Api, desc = "营收总额")
    @RequestMapping(value = "api/fitness/storeData/totalRevenue", method = RequestMethod.GET)
    Integer totalRevenue(
            @AuthenticationPrincipal FrogUserDetails frogUserDetails,
            @RequestParam(value = "startDate") String startDate,
            @RequestParam(value = "endDate") String endDate,
            @RequestParam(value = "coachId", required = false) String coachId,
            @RequestParam(value = "type", required = false) String type
    ) throws ParseException {
        Integer totalRevenue = storeDataService.totalRevenue(startDate,endDate,coachId,type);
        return totalRevenue;
    }



}

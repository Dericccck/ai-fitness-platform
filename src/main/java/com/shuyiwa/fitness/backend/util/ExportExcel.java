package com.shuyiwa.fitness.backend.util;

import org.apache.poi.hssf.usermodel.*;
import org.apache.poi.hssf.util.HSSFColor;
import org.apache.poi.ss.usermodel.*;

import java.io.IOException;
import java.io.OutputStream;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.text.SimpleDateFormat;
import java.util.Collection;
import java.util.Date;
import java.util.Iterator;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 利用开源组件POI3.0.2动态导出EXCEL文档
 * @author shily
 * @param <T> 应用泛型，代表任意一个符合javabean风格的类
 * 注意这里为了简单起见，boolean型的属性xxx的get器方式为getXxx(),而不是isXxx()
 * byte[]表jpg格式的图片数据
 */

public class ExportExcel<T> {
 
   public void exportExcel(Collection<T> dataset, OutputStream out) {
      exportExcel("报表统计", null, dataset, out, "yyyy-MM-dd");
   }
 
   public void exportExcel(String[] headers, Collection<T> dataset,
         OutputStream out) {
      exportExcel("报表统计", headers, dataset, out, "yyyy-MM-dd");
   }
 
   public void exportExcel(String[] headers, Collection<T> dataset,
         OutputStream out, String pattern) {
      exportExcel("报表统计", headers, dataset, out, pattern);
   }
 
   public void exportExcel(String[] headers,String[] fileds, Collection<T> dataset,
	         OutputStream out) {
	      exportExcel("报表统计", headers, fileds,dataset, out, "yyyy-MM-dd");
	   }

    public void exportExcel(String title,String[] headers,String[] fileds, Collection<T> dataset,
                            OutputStream out) {
        exportExcel(title, headers, fileds,dataset, out, "yyyy-MM-dd");
    }

   public void exportExcel(String[] headers,String[] fileds, Collection<T> dataset,
                            OutputStream out, String pattern) {
        exportExcel("报表统计", headers, fileds,dataset, out, pattern);
    }
   
   /**
    * 这是一个通用的方法，利用了JAVA的反射机制，可以将放置在JAVA集合中并且符号一定条件的数据以EXCEL 的形式输出到指定IO设备上
    *
    * @param title
    *            表格标题名
    * @param headers
    *            表格属性列名数组
    * @param dataset
    *            需要显示的数据集合,集合中一定要放置符合javabean风格的类的对象。此方法支持的
    *            javabean属性的数据类型有基本数据类型及String,Date,byte[](图片数据)
    * @param out
    *            与输出设备关联的流对象，可以将EXCEL文档导出到本地文件或者网络中
    * @param pattern
    *            如果有时间数据，设定输出格式。默认为"yyy-MM-dd"
    */
   @SuppressWarnings("unchecked")
   public void exportExcel(String title, String[] headers,
         Collection<T> dataset, OutputStream out, String pattern) {
      // 声明一个工作薄
      HSSFWorkbook workbook = new HSSFWorkbook();
      // 生成一个表格
      HSSFSheet sheet = workbook.createSheet(title);
      // 设置表格默认列宽度为15个字节
      sheet.setDefaultColumnWidth(15);
      // 生成一个样式
      HSSFCellStyle style = workbook.createCellStyle();
      // 设置这些样式
      style.setFillForegroundColor(HSSFColor.HSSFColorPredefined.WHITE.getIndex());
//      style.setFillPattern(HSSFCellStyle.SOLID_FOREGROUND);
//      style.setBorderBottom(HSSFCellStyle.BORDER_THIN);
//      style.setBorderLeft(HSSFCellStyle.BORDER_THIN);
//      style.setBorderRight(HSSFCellStyle.BORDER_THIN);
//      style.setBorderTop(HSSFCellStyle.BORDER_THIN);
//      style.setAlignment(HSSFCellStyle.ALIGN_CENTER);
       style.setWrapText(true);
       style.setFillPattern(FillPatternType.SOLID_FOREGROUND);
       style.setBorderBottom(BorderStyle.THIN);
       style.setBorderLeft(BorderStyle.THIN);
       style.setBorderRight(BorderStyle.THIN);
       style.setBorderTop(BorderStyle.THIN);
       style.setAlignment(HorizontalAlignment.CENTER);


      // 生成一个字体
      HSSFFont font = workbook.createFont();
      font.setColor(HSSFColor.HSSFColorPredefined.BLACK.getIndex());
//      font.setFontHeightInPoints((short) 12);
//       font.setFontHeight((short) 12);
//      font.setBoldweight(HSSFFont.BOLDWEIGHT_BOLD);
      // 把字体应用到当前的样式
      style.setFont(font);
      // 生成并设置另一个样式
      HSSFCellStyle style2 = workbook.createCellStyle();
      style2.setFillForegroundColor(HSSFColor.HSSFColorPredefined.WHITE.getIndex());
//      style2.setFillPattern(HSSFCellStyle.SOLID_FOREGROUND);
//      style2.setBorderBottom(HSSFCellStyle.BORDER_THIN);
//      style2.setBorderLeft(HSSFCellStyle.BORDER_THIN);
//      style2.setBorderRight(HSSFCellStyle.BORDER_THIN);
//      style2.setBorderTop(HSSFCellStyle.BORDER_THIN);
//      style2.setAlignment(HSSFCellStyle.ALIGN_CENTER);
//      style2.setVerticalAlignment(HSSFCellStyle.VERTICAL_CENTER);

       style2.setFillPattern(FillPatternType.SOLID_FOREGROUND);
       style2.setBorderBottom(BorderStyle.THIN);
       style2.setBorderLeft(BorderStyle.THIN);
       style2.setBorderRight(BorderStyle.THIN);
       style2.setBorderTop(BorderStyle.THIN);
       style2.setAlignment(HorizontalAlignment.CENTER);
       style2.setVerticalAlignment(VerticalAlignment.CENTER);

      // 生成另一个字体
      HSSFFont font2 = workbook.createFont();
      font2.setColor(HSSFColor.HSSFColorPredefined.BLACK.getIndex());
//      font2.setBoldweight(HSSFFont.BOLDWEIGHT_NORMAL);
//       font2.setFontHeight(HSSFFont.COLOR_NORMAL);
      // 把字体应用到当前的样式
      style2.setFont(font2);
     
      // 声明一个画图的顶级管理器
      HSSFPatriarch patriarch = sheet.createDrawingPatriarch();
      // 定义注释的大小和位置,详见文档
      HSSFComment comment = patriarch.createComment(new HSSFClientAnchor(0, 0, 0, 0, (short) 4, 2, (short) 6, 5));
      // 设置注释内容
      comment.setString(new HSSFRichTextString("可以在POI中添加注释！"));
      // 设置注释作者，当鼠标移动到单元格上是可以在状态栏中看到该内容.
      comment.setAuthor("dx");
 
      //产生表格标题行
      HSSFRow row = sheet.createRow(0);
      for (int i = 0; i < headers.length; i++) {
         HSSFCell cell = row.createCell(i);
         cell.setCellStyle(style);
         HSSFRichTextString text = new HSSFRichTextString(headers[i]);
         cell.setCellValue(text);
      }
 
      //遍历集合数据，产生数据行
      Iterator<T> it = dataset.iterator();
      int index = 0;
      while (it.hasNext()) {
         index++;
         row = sheet.createRow(index);
         T t = (T) it.next();
         //利用反射，根据javabean属性的先后顺序，动态调用getXxx()方法得到属性值
         Field[] fields = t.getClass().getDeclaredFields();
         for (int i = 0; i < fields.length; i++) {
            HSSFCell cell = row.createCell(i);
            cell.setCellStyle(style2);
            Field field = fields[i];
            String fieldName = field.getName();
            String getMethodName = "get"
                   + fieldName.substring(0, 1).toUpperCase()
                   + fieldName.substring(1);
            try {
                Class tCls = t.getClass();
                Method getMethod = tCls.getMethod(getMethodName,
                      new Class[] {});
                Object value = getMethod.invoke(t, new Object[] {});
                //判断值的类型后进行强制类型转换
                String textValue = null;
                if (value instanceof Boolean) {
                   boolean bValue = (Boolean) value;
                   textValue = "男";
                   if (!bValue) {
                      textValue ="女";
                   }
                } else if (value instanceof Date) {
                   Date date = (Date) value;
                   SimpleDateFormat sdf = new SimpleDateFormat(pattern);
                    textValue = sdf.format(date);
                }  else if (value instanceof byte[]) {
                   // 有图片时，设置行高为60px;
                   row.setHeightInPoints(60);
                   // 设置图片所在列宽度为80px,注意这里单位的一个换算
                   sheet.setColumnWidth(i, (short) (35.7 * 80));
                   // sheet.autoSizeColumn(i);
                   byte[] bsValue = (byte[]) value;
                   HSSFClientAnchor anchor = new HSSFClientAnchor(0, 0,
                         1023, 255, (short) 6, index, (short) 6, index);
//                   anchor.setAnchorType(2);
                   patriarch.createPicture(anchor, workbook.addPicture(
                         bsValue, HSSFWorkbook.PICTURE_TYPE_JPEG));
                } else{
                   //其它数据类型都当作字符串简单处理
                   textValue = value.toString();
                }
                //如果不是图片数据，就利用正则表达式判断textValue是否全部由数字组成
                if(textValue!=null){
                   Pattern p = Pattern.compile("^//d+(//.//d+)?$");  
                   Matcher matcher = p.matcher(textValue);
                   if(matcher.matches()){
                      //是数字当作double处理
                      cell.setCellValue(Double.parseDouble(textValue));
                   }else{
                      HSSFRichTextString richString = new HSSFRichTextString(textValue);
                      // workbook的字体数目有上限，不能创建太多字体，而且同样的字体格式应该复用
//                      HSSFFont font3 = workbook.createFont();
//                      font3.setColor(HSSFColor.BLACK.index);
                      richString.applyFont(font2);
                      cell.setCellValue(richString);
                   }
                }
            } catch (SecurityException e) {
                // TODO Auto-generated catch block
                e.printStackTrace();
            } catch (NoSuchMethodException e) {
                // TODO Auto-generated catch block
                e.printStackTrace();
            } catch (IllegalArgumentException e) {
                // TODO Auto-generated catch block
                e.printStackTrace();
            } catch (IllegalAccessException e) {
                // TODO Auto-generated catch block
                e.printStackTrace();
            } catch (InvocationTargetException e) {
                // TODO Auto-generated catch block
                e.printStackTrace();
            } finally {
                //清理资源
            }
         }
 
      }
      try {
         workbook.write(out);
      } catch (IOException e) {
         // TODO Auto-generated catch block
         e.printStackTrace();
      }
   }
   
   /**
    * 这是一个通用的方法，利用了JAVA的反射机制，可以将放置在JAVA集合中并且符号一定条件的数据以EXCEL 的形式输出到指定IO设备上
    *
    * @param title
    *            表格标题名
    * @param headers
    *            表格属性列名数组
    * @param fileds
    * 			 类中需要在表格中展示的字段           
    * @param dataset
    *            需要显示的数据集合,集合中一定要放置符合javabean风格的类的对象。此方法支持的
    *            javabean属性的数据类型有基本数据类型及String,Date,byte[](图片数据)
    * @param out
    *            与输出设备关联的流对象，可以将EXCEL文档导出到本地文件或者网络中
    * @param pattern
    *            如果有时间数据，设定输出格式。默认为"yyy-MM-dd"
    */
   @SuppressWarnings("unchecked")
   public void exportExcel(String title, String[] headers,String [] fileds,
         Collection<T> dataset, OutputStream out, String pattern) {
      // 声明一个工作薄
      HSSFWorkbook workbook = new HSSFWorkbook();
      // 生成一个表格
      HSSFSheet sheet = workbook.createSheet(title);
      // 设置表格默认列宽度为15个字节
      sheet.setDefaultColumnWidth(15);
      // 生成一个样式
      HSSFCellStyle style = workbook.createCellStyle();
      // 设置这些样式
//      style.setFillForegroundColor(HSSFColor.WHITE.index);
       style.setFillForegroundColor(HSSFColor.HSSFColorPredefined.GREY_25_PERCENT.getIndex());
//      style.setFillPattern(HSSFCellStyle.SOLID_FOREGROUND);
//      style.setBorderBottom(HSSFCellStyle.BORDER_THIN);
//      style.setBorderLeft(HSSFCellStyle.BORDER_THIN);
//      style.setBorderRight(HSSFCellStyle.BORDER_THIN);
//      style.setBorderTop(HSSFCellStyle.BORDER_THIN);
//      style.setAlignment(HSSFCellStyle.ALIGN_CENTER);

       style.setFillPattern(FillPatternType.SOLID_FOREGROUND);
       style.setBorderBottom(BorderStyle.THIN);
       style.setBorderLeft(BorderStyle.THIN);
       style.setBorderRight(BorderStyle.THIN);
       style.setBorderTop(BorderStyle.THIN);
       style.setAlignment(HorizontalAlignment.CENTER);

      // 生成一个字体
      HSSFFont font = workbook.createFont();
      font.setColor(HSSFColor.HSSFColorPredefined.BLACK.getIndex());
//       font.setFontHeight((short) 12);
//      font.setBoldweight(HSSFFont.BOLDWEIGHT_BOLD);
//       font.setFontHeight(HSSFFont.COLOR_NORMAL);
      // 把字体应用到当前的样式
      style.setFont(font);
      // 生成并设置另一个样式
      HSSFCellStyle style2 = workbook.createCellStyle();
      style2.setFillForegroundColor(HSSFColor.HSSFColorPredefined.WHITE.getIndex());
//      style2.setFillPattern(HSSFCellStyle.SOLID_FOREGROUND);
//      style2.setBorderBottom(HSSFCellStyle.BORDER_THIN);
//      style2.setBorderLeft(HSSFCellStyle.BORDER_THIN);
//      style2.setBorderRight(HSSFCellStyle.BORDER_THIN);
//      style2.setBorderTop(HSSFCellStyle.BORDER_THIN);
//      style2.setAlignment(HSSFCellStyle.ALIGN_CENTER);
//      style2.setVerticalAlignment(HSSFCellStyle.VERTICAL_CENTER);

       style2.setFillPattern(FillPatternType.SOLID_FOREGROUND);
       style2.setBorderBottom(BorderStyle.THIN);
       style2.setBorderLeft(BorderStyle.THIN);
       style2.setBorderRight(BorderStyle.THIN);
       style2.setBorderTop(BorderStyle.THIN);
       style2.setAlignment(HorizontalAlignment.CENTER);
       style2.setVerticalAlignment(VerticalAlignment.CENTER);

      // 生成另一个字体
      HSSFFont font2 = workbook.createFont();
      font2.setColor(HSSFColor.HSSFColorPredefined.BLACK.getIndex());
//      font2.setBoldweight(HSSFFont.BOLDWEIGHT_NORMAL);
//       font2.setFontHeight(HSSFFont.COLOR_NORMAL);
      // 把字体应用到当前的样式
      style2.setFont(font2);
      
      //数值的数据格式
      HSSFCellStyle numberCellStyle = workbook.createCellStyle();
//      numberCellStyle.setAlignment(HSSFCellStyle.ALIGN_RIGHT);
      numberCellStyle.setFillForegroundColor(HSSFColor.HSSFColorPredefined.WHITE.getIndex());
//      numberCellStyle.setFillPattern(HSSFCellStyle.SOLID_FOREGROUND);
//      numberCellStyle.setBorderBottom(HSSFCellStyle.BORDER_THIN);
//      numberCellStyle.setBorderLeft(HSSFCellStyle.BORDER_THIN);
//      numberCellStyle.setBorderRight(HSSFCellStyle.BORDER_THIN);
//      numberCellStyle.setBorderTop(HSSFCellStyle.BORDER_THIN);
//      numberCellStyle.setVerticalAlignment(HSSFCellStyle.VERTICAL_CENTER);

       numberCellStyle.setFillPattern(FillPatternType.SOLID_FOREGROUND);
       numberCellStyle.setBorderBottom(BorderStyle.THIN);
       numberCellStyle.setBorderLeft(BorderStyle.THIN);
       numberCellStyle.setBorderRight(BorderStyle.THIN);
       numberCellStyle.setBorderTop(BorderStyle.THIN);
       numberCellStyle.setAlignment(HorizontalAlignment.CENTER);
       numberCellStyle.setVerticalAlignment(VerticalAlignment.CENTER);
     
      HSSFCellStyle intCellStyle = workbook.createCellStyle();
//      intCellStyle.setAlignment(HSSFCellStyle.ALIGN_RIGHT);
//      intCellStyle.setBorderBottom(HSSFCellStyle.BORDER_THIN);
//      intCellStyle.setBorderLeft(HSSFCellStyle.BORDER_THIN);
//      intCellStyle.setBorderRight(HSSFCellStyle.BORDER_THIN);
//      intCellStyle.setBorderTop(HSSFCellStyle.BORDER_THIN);

       intCellStyle.setFillPattern(FillPatternType.SOLID_FOREGROUND);
       intCellStyle.setBorderBottom(BorderStyle.THIN);
       intCellStyle.setBorderLeft(BorderStyle.THIN);
       intCellStyle.setBorderRight(BorderStyle.THIN);
       intCellStyle.setBorderTop(BorderStyle.THIN);
      
      HSSFDataFormat df=workbook.createDataFormat();
      
      
      // 声明一个画图的顶级管理器
      HSSFPatriarch patriarch = sheet.createDrawingPatriarch();
      // 定义注释的大小和位置,详见文档
      HSSFComment comment = patriarch.createComment(new HSSFClientAnchor(0, 0, 0, 0, (short) 4, 2, (short) 6, 5));
      // 设置注释内容
      comment.setString(new HSSFRichTextString("可以在POI中添加注释！"));
      // 设置注释作者，当鼠标移动到单元格上是可以在状态栏中看到该内容.
      comment.setAuthor("DX");
 
      //产生表格标题行
      HSSFRow row = sheet.createRow(0);
      for (int i = 0; i < headers.length; i++) {
         HSSFCell cell = row.createCell(i);
         cell.setCellStyle(style);
         HSSFRichTextString text = new HSSFRichTextString(headers[i]);
         cell.setCellValue(text);
      }
 
      //遍历集合数据，产生数据行
      if(dataset!=null){
    	  Iterator<T> it = dataset.iterator();
          int index = 0;
          while (it.hasNext()) {
             index++;
             row = sheet.createRow(index);
             T t = (T) it.next();
             //利用反射，根据javabean属性的先后顺序，动态调用getXxx()方法得到属性值
            // Field[] fields = t.getClass().getDeclaredFields();
             for (int i = 0; i < fileds.length; i++) {
                HSSFCell cell = row.createCell(i);
                cell.setCellStyle(style2);
               // Field field = fields[i];
                String fieldName = fileds[i];

                try {
                    Object value = null;
                    if(t instanceof Map){
                        value = ((Map)t).get(fieldName);
                    }else {
                        String getMethodName = "get"
                                + fieldName.substring(0, 1).toUpperCase()
                                + fieldName.substring(1);
                        Class tCls = t.getClass();
                        Method getMethod = tCls.getMethod(getMethodName,
                                new Class[] {});
                        value = getMethod.invoke(t, new Object[] {});
                    }

                    //各种率，带%或千分号后缀
                    if(containsRateFiled(fieldName)){
                    	String textValue=null;
                    	 if(value!=null){
                			 textValue=((BigDecimal) value).toString();
                    		 String suffix=addSuffix(fieldName);
                    		 if(!"".equals(suffix)){
                    			 textValue+=suffix;
                    		 }
                    		 cell.setCellValue(textValue);
                    		 cell.setCellType(CellType.STRING);
                    	}else{
                    		 cell.setCellValue("");
                    	}
                    	 cell.setCellStyle(intCellStyle);
                    	 //带2位小数
                    }else if(hasPrice(fieldName)){
                    	numberCellStyle.setDataFormat(HSSFDataFormat.getBuiltinFormat("0.00"));
                    	if(value!=null){
                    		double doubleValue=((BigDecimal) value).doubleValue();
                    		cell.setCellValue(doubleValue);
                    	}else{
                    		cell.setCellValue("");
                    	}
                    	cell.setCellStyle(numberCellStyle);
                	    //各种汇总数
                    }else if(hasCnt(fieldName)){
                    	if(value!=null){
                    		cell.setCellStyle(intCellStyle);
                    		int intValue=((BigDecimal) value).intValue();
                    		cell.setCellValue(intValue);
                    	}else{
                    		cell.setCellValue("");
                    	}
                    }else{
                    	if(value==null||"null".equals(value))value="";
                    	if (value instanceof Date) {
                    		Date date = (Date) value;
                    		SimpleDateFormat sdf = new SimpleDateFormat(pattern);
                    		value = sdf.format(date);
                    	}
                    	HSSFRichTextString richString = new HSSFRichTextString(value.toString());
                        richString.applyFont(font2);
                        cell.setCellValue(richString);
                    }
                } catch (SecurityException e) {
                    // TODO Auto-generated catch block
                    e.printStackTrace();
                } catch (NoSuchMethodException e) {
                    // TODO Auto-generated catch block
                    e.printStackTrace();
                } catch (IllegalArgumentException e) {
                    // TODO Auto-generated catch block
                    e.printStackTrace();
                } catch (IllegalAccessException e) {
                    // TODO Auto-generated catch block
                    e.printStackTrace();
                } catch (InvocationTargetException e) {
                    // TODO Auto-generated catch block
                    e.printStackTrace();
                } finally {
                    //清理资源
                }
             }
     
          }
      }
      
      try {
         workbook.write(out);
      } catch (IOException e) {
         // TODO Auto-generated catch block
         e.printStackTrace();
      }
   }
   
   /**
    * 给指定字段的值添加后缀
    * @param filedName
    * @return
    */
   private String addSuffix(String filedName){
	   String result="";
	   String [] rates={"bidSuccessRate","arriveRate","transRate","secondJumpCntRate","clickRate"};
	   for(String filed :rates){
		   if(filedName.equals(filed)){
			   result="%";
			   /*if(filedName.equals("clickRate")){
				   result="‰";
			   }else{
				   result="%";
			   }*/
		   }   
	   }
	   
	   return result;
   }
   
   private boolean containsRateFiled(String filedName){
	   String [] rates={"bidSuccessRate","arriveRate","transRate","secondJumpCntRate","clickRate"};
	   for(String filed :rates){
		   if(filedName.equals(filed)){
			   return true;
		   }
	   }
	   return false;
   }   
   /**
    * 是否为价格或花费字段
    * @param filedName
    * @return
    */
   private boolean hasPrice(String filedName){
	   if(filedName.toLowerCase().endsWith("price")||filedName.toLowerCase().endsWith("cost")){
		   return true;
	   }else{
		   return false;
	   }
   }
   
   /**
    * 是否为汇总字段
    * @param filedName
    * @return
    */
   private boolean hasCnt(String filedName){
	   if(filedName.toLowerCase().endsWith("cnt")){
		   return true;
	   }else{
		   return false;
	   }
   }
   
   
   
}

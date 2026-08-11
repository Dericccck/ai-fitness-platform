package com.shuyiwa.fitness.backend.util;

import com.google.zxing.*;
import com.google.zxing.client.j2se.BufferedImageLuminanceSource;
import com.google.zxing.common.BitMatrix;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel;

import javax.imageio.ImageIO;
import java.awt.*;
import java.awt.geom.RoundRectangle2D;
import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.util.Hashtable;


public class QRCodeUtil {
    private static final String CHARSET = "UTF-8";
    private static final String FORMAT_NAME = "JPG";
    // 二维码尺寸  
    private static final int QRCODE_SIZE = 500;
    // LOGO宽度  
    private static final int WIDTH = 120;
    // LOGO高度  
    private static final int HEIGHT = 120;


    /**
     * user: Rex
     * date: 2016年12月29日  上午12:31:29
     *
     * @param content      二维码内容
     * @param logoImgPath  Logo
     * @param needCompress 是否压缩Logo
     * @return 返回二维码图片
     * @throws WriterException
     * @throws IOException     BufferedImage
     *                         TODO 创建二维码图片
     */
    public static BufferedImage createImage(String content, String logoImgPath, boolean needCompress) throws WriterException, IOException {
        return createImage(content, null, logoImgPath, needCompress);
    }

    /**
     * user: Rex
     * date: 2016年12月29日  上午12:31:29
     *
     * @param content      二维码内容
     * @param logoImgPath  Logo
     * @param needCompress 是否压缩Logo
     * @return 返回二维码图片
     * @throws WriterException
     * @throws IOException     BufferedImage
     *                         TODO 创建二维码图片
     */
    public static BufferedImage createImage(String content, String title, String logoImgPath, boolean needCompress) throws WriterException, IOException {
        Hashtable<EncodeHintType, Object> hints = new Hashtable<EncodeHintType, Object>();
        hints.put(EncodeHintType.ERROR_CORRECTION, ErrorCorrectionLevel.H);
        hints.put(EncodeHintType.CHARACTER_SET, CHARSET);
        hints.put(EncodeHintType.MARGIN, 1);
        BitMatrix bitMatrix = new MultiFormatWriter().encode(content, BarcodeFormat.QR_CODE, QRCODE_SIZE, QRCODE_SIZE, hints);
        int width = bitMatrix.getWidth();
        int height = bitMatrix.getHeight();
        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
        for (int x = 0; x < width; x++) {
            for (int y = 0; y < height; y++) {
                image.setRGB(x, y, bitMatrix.get(x, y) ? 0xFF000000 : 0xFFFFFFFF);
            }
        }
        // 插入图片
        if (logoImgPath != null && !"".equals(logoImgPath)) {
            QRCodeUtil.insertImage(image, logoImgPath, needCompress);
        }
        //增加标题
        if (title != null && !"".equals(title)) {
            image = addTitle(image, title);
        }

        return image;
    }

    private static BufferedImage addTitle(BufferedImage source, String title) {
        if (title == null || "".equals(title)) {
            return source;
        }
        //只保留12个字
        if (title.length() > 12) {
            title = title.substring(0, 12);
        }
        int titleHeight = 80;
        int width = source.getWidth();
        int height = source.getHeight();
        BufferedImage image = new BufferedImage(width, height + titleHeight, BufferedImage.TYPE_INT_RGB);
        for (int x = 0; x < width; x++) {
            for (int y = 0; y < image.getHeight(); y++) {
                image.setRGB(x, y, 0xFFFFFFFF);
            }
        }
        Graphics2D graph = image.createGraphics();
        graph.setColor(Color.BLACK);
        //设置字体
        Font font = new Font("宋体", Font.PLAIN, 40);
        graph.setFont(font);
        //向画板上标题，剧中对齐
        FontMetrics fm = graph.getFontMetrics(font);
        int textWidth = fm.stringWidth(title);
        int widthX = (width - textWidth) / 2;

        graph.drawString(title, widthX, 60);

        // 插入二维码
        int x = 0;
        int y = titleHeight;
        Image src = source.getScaledInstance(source.getWidth(), source.getHeight(), Image.SCALE_SMOOTH);
        graph.drawImage(src, x, y, width, height, null);
        Shape shape = new RoundRectangle2D.Float(x, y, width, width, 6, 6);
//        graph.setStroke(new BasicStroke(3f));
        graph.draw(shape);
        graph.dispose();
        return image;
    }

    /**
     * user: Rex
     * date: 2016年12月29日  上午12:30:09
     *
     * @param source       二维码图片
     * @param logoImgPath  Logo
     * @param needCompress 是否压缩Logo
     * @throws IOException void
     *                     TODO 添加Logo
     */
    private static void insertImage(BufferedImage source, String logoImgPath, boolean needCompress) throws IOException {
        File file = new File(logoImgPath);
        if (!file.exists()) {
            return;
        }

        Image src = ImageIO.read(new File(logoImgPath));
        int width = src.getWidth(null);
        int height = src.getHeight(null);
        if (needCompress) { // 压缩LOGO  
            if (width > WIDTH) {
                width = WIDTH;
            }

            if (height > HEIGHT) {
                height = HEIGHT;
            }

            Image image = src.getScaledInstance(width, height, Image.SCALE_SMOOTH);
            BufferedImage tag = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
            Graphics g = tag.getGraphics();
            g.drawImage(image, 0, 0, null); // 绘制缩小后的图  
            g.dispose();
            src = image;
        }

        // 插入LOGO  
        Graphics2D graph = source.createGraphics();
        int x = (QRCODE_SIZE - width) / 2;
        int y = (QRCODE_SIZE - height) / 2;
        graph.drawImage(src, x, y, width, height, null);
        Shape shape = new RoundRectangle2D.Float(x, y, width, width, 6, 6);
        graph.setStroke(new BasicStroke(3f));
        graph.draw(shape);
        graph.dispose();
    }

    /**
     * user: Rex
     * date: 2016年12月29日  上午12:32:32
     *
     * @param content      二维码内容
     * @param logoImgPath  Logo
     * @param destPath     二维码输出路径
     * @param needCompress 是否压缩Logo
     * @throws Exception void
     *                   TODO 生成带Logo的二维码
     */
    public static void encode(String content, String logoImgPath, String destPath, boolean needCompress) throws Exception {
        BufferedImage image = QRCodeUtil.createImage(content, logoImgPath, needCompress);
        FileUtil.mkdirs(destPath);
        ImageIO.write(image, FORMAT_NAME, new File(destPath));
    }

    /**
     * user: Rex
     * date: 2016年12月29日  上午12:35:44
     *
     * @param content  二维码内容
     * @param destPath 二维码输出路径
     * @throws Exception void
     *                   TODO 生成不带Logo的二维码
     */
    public static void encode(String content, String destPath) throws Exception {
        QRCodeUtil.encode(content, null, destPath, false);
    }

    /**
     * user: Rex
     * date: 2016年12月29日  上午12:36:58
     *
     * @param content      二维码内容
     * @param logoImgPath  Logo
     * @param output       输出流
     * @param needCompress 是否压缩Logo
     * @throws Exception void
     *                   TODO 生成带Logo的二维码，并输出到指定的输出流
     */
    public static void encode(String content, String logoImgPath, OutputStream output, boolean needCompress) throws Exception {
        BufferedImage image = QRCodeUtil.createImage(content, logoImgPath, needCompress);
        ImageIO.write(image, FORMAT_NAME, output);
    }

    /**
     * user: Rex
     * date: 2016年12月29日  上午12:38:02
     *
     * @param content 二维码内容
     * @param output  输出流
     * @throws Exception void
     *                   TODO 生成不带Logo的二维码，并输出到指定的输出流
     */
    public static void encode(String content, OutputStream output) throws Exception {
        QRCodeUtil.encode(content, null, output, false);
    }

    /**
     * user: Rex
     * date: 2016年12月29日  上午12:39:10
     *
     * @param file 二维码
     * @return 返回解析得到的二维码内容
     * @throws Exception String
     *                   TODO 二维码解析
     */
    public static String decode(File file) throws Exception {
        BufferedImage image;
        image = ImageIO.read(file);
        if (image == null) {
            return null;
        }
        BufferedImageLuminanceSource source = new BufferedImageLuminanceSource(image);
        BinaryBitmap bitmap = new BinaryBitmap(new HybridBinarizer(source));
        Result result;
        Hashtable<DecodeHintType, Object> hints = new Hashtable<DecodeHintType, Object>();
        hints.put(DecodeHintType.CHARACTER_SET, CHARSET);
        result = new MultiFormatReader().decode(bitmap, hints);
        String resultStr = result.getText();
        return resultStr;
    }

    /**
     * user: Rex
     * date: 2016年12月29日  上午12:39:48
     *
     * @param path 二维码存储位置
     * @return 返回解析得到的二维码内容
     * @throws Exception String
     *                   TODO 二维码解析
     */
    public static String decode(String path) throws Exception {
        return QRCodeUtil.decode(new File(path));
    }
}
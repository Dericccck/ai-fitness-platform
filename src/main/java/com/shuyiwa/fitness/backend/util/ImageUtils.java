package com.shuyiwa.fitness.backend.util;

import com.google.zxing.WriterException;
import org.springframework.util.StringUtils;

import javax.imageio.ImageIO;
import javax.servlet.ServletResponse;
import java.awt.*;
import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.net.URL;

public class ImageUtils {
    public static void main(String[] args) {
        String url="https://console.fitooss.com";
        String name="";
        String logoFile = null;
        try {
            BufferedImage qrCode = QRCodeUtil.createImage(url, name, logoFile, true);
            BufferedImage background = null;
            try{
                background = ImageIO.read(new URL("https://img.shuyiwa.com/contest/season/postbackground/40288a8d7544a99f0175549896125578.png"));
            }catch (Exception e){
                background = ImageIO.read(new URL("https://img.shuyiwa.com/contest/season/postbackground/default.png"));
            }
            //ImageIO.read(new File("C:\\Users\\wuson\\Pictures\\Saved Pictures\\2021.png"));
            overLapImage(background,qrCode,"","","C:\\Users\\wuson\\Pictures\\Saved Pictures\\123.jpg");
        } catch (WriterException e) {
            e.printStackTrace();
        } catch (IOException e) {
            e.printStackTrace();
        }

    }


    public static void overLapImage(BufferedImage backgroundImg, BufferedImage qrCode, String message01, String message02, ServletResponse response){
        try {
            BufferedImage background = resizeImage(backgroundImg.getWidth(), backgroundImg.getHeight(), backgroundImg);
            Graphics2D g = background.createGraphics();
            if(!StringUtils.isEmpty(message01) || !StringUtils.isEmpty(message02)) {
                g.setColor(Color.WHITE);
                g.setFont(new Font("微软雅黑", Font.BOLD, 20));
                g.drawString(message01, 530, 190);
                g.drawString(message02, 530, 220);
            }
            BufferedImage qrCode2 = resizeImage(110,110,qrCode);

            g.drawImage(qrCode2, background.getWidth()-qrCode2.getWidth()-40, background.getHeight()-qrCode2.getHeight()-15, qrCode2.getWidth(), qrCode2.getHeight(), null);
            g.dispose();

            response.setContentType("image/jpg");
            ImageIO.write(background, "jpg", response.getOutputStream());
        }catch (Exception e){
            e.printStackTrace();
        }

    }

    public static String overLapImage(BufferedImage backgroundImg,BufferedImage qrCode,String message01,String message02,String outputPath){
        try {
            BufferedImage background = resizeImage(backgroundImg.getWidth(), backgroundImg.getHeight(), backgroundImg);
            Graphics2D g = background.createGraphics();
            if(!StringUtils.isEmpty(message01) || !StringUtils.isEmpty(message02)) {
                g.setColor(Color.WHITE);
                g.setFont(new Font("微软雅黑", Font.BOLD, 20));
                g.drawString(message01, 530, 190);
                g.drawString(message02, 530, 220);
            }
            BufferedImage qrCode2 = resizeImage(110,110,qrCode);

            g.drawImage(qrCode2, background.getWidth()-qrCode2.getWidth()-40, background.getHeight()-qrCode2.getHeight()-15, qrCode2.getWidth(), qrCode2.getHeight(), null);
            g.dispose();
            ImageIO.write(background, "jpg", new File(outputPath));
        }catch (Exception e){
            e.printStackTrace();
        }
        return null;
    }


    public static String overLapImage(String backgroundImgPath,String qrCodePath,String message01,String message02,String outputPath){
        try {
            BufferedImage background = resizeImage(1000, 618, ImageIO.read(new File(backgroundImgPath)));
            BufferedImage qrCode = resizeImage(150, 150, ImageIO.read(new File(qrCodePath)));

            Graphics2D g = background.createGraphics();
            g.setColor(Color.WHITE);
            g.setFont(new Font("微软雅黑", Font.BOLD, 20));
            g.drawString(message01, 530, 190);
            g.drawString(message02, 530, 220);

            g.drawImage(qrCode, 700, 240, qrCode.getWidth(), qrCode.getHeight(), null);
            g.dispose();
            ImageIO.write(background, "jpg", new File(outputPath));
        }catch (Exception e){
            e.printStackTrace();
        }
        return null;
    }

    public static BufferedImage resizeImage(int x,int y,BufferedImage bfi){
        BufferedImage bufferedImage =  new BufferedImage(x,y,BufferedImage.TYPE_INT_RGB);
        bufferedImage.getGraphics().drawImage(bfi.getScaledInstance(x,y, Image.SCALE_SMOOTH),0,0,null);
        return bufferedImage;
    }
}

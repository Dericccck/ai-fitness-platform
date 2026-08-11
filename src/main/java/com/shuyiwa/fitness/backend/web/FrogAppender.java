package com.shuyiwa.fitness.backend.web;

import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.AppenderBase;

public class FrogAppender extends AppenderBase<ILoggingEvent> {


    @Override
    protected void append(ILoggingEvent event) {
//        try {
//            String formattedMessage = event.getFormattedMessage();
//            if (formattedMessage != null && formattedMessage.contains("id1_28_0_")) {
//                System.out.println("aa:" + formattedMessage);
//                if(System.currentTimeMillis()%20==1){
//                    new Exception().printStackTrace();
//                }
//
//            }
//        } catch (Exception e) {
//            e.printStackTrace();
//        }
    }

}
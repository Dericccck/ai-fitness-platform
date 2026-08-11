package com.shuyiwa.fitness.backend.global;

public class FrogException extends RuntimeException {
    public static final int OK = 200;
    public static final int UNAUTHORIZED = 401;
    public static final int FORBIDDEN = 403;
    public static final int INTERNAL_SERVER_ERROR = 500;
    public static final int IMG_VERIFY_CODE_NOT_MATCH = 501;
    public static final int PHONE_VERIFY_CODE_SEND_TOO_MANY_TIMES = 502;
    public static final int PHONE_VERIFY_CODE_SEND_Exception = 503;
    public static final int PHONE_VERIFY_CODE_SEND_FAILED = 504;
    public static final int MAX_UPLOAD_SIZE_EXCEEDED_EXCEPTION = 505;
    public static final int SAVE_UPLOAD_FILE_FAILED = 506;
    public static final int UNSUPPORTED_FILE_FORMAT = 507;
    public static final int SAVE_WORKS_FAILED = 508;
    public static final int DEL_UPLOAD_FILE_FAILED = 509;
    public static final int WEI_XIN_UNBIND_TOO_QUICK = 510;
    public static final int LOGINUSER_NO_EXIST = 601;
    public static final int LOGINUSER_PHONE_EXIST = 602;
    public static final int ITEM_NOT_VALIDATE = 701;
    public static final int BALANCE_NOT_ENOUGH = 702;
    public static final int BUY_USER_LIMIT = 703;
    public static final int PAY = 801;
    public static final int PAY_INNER = 802;
    private final int code;
    private String innerMessage;

    public FrogException(int code, String message) {
        super(message);
        this.code = code;
    }

    public FrogException(int code, String message, String innerMessage) {
        super(message);
        this.innerMessage = innerMessage;
        this.code = code;
    }

    public FrogException(int code, String message, Throwable cause) {
        super(message, cause);
        this.code = code;
    }

    public String getInnerMessage() {
        return innerMessage;
    }

    public void setInnerMessage(String innerMessage) {
        this.innerMessage = innerMessage;
    }

    public int getCode() {
        return code;
    }
}

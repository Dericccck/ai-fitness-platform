package com.shuyiwa.fitness.backend.conf;

import org.hibernate.boot.Metadata;
import org.hibernate.dialect.MySQL55Dialect;
import org.hibernate.dialect.function.SQLFunctionTemplate;
import org.hibernate.mapping.Table;
import org.hibernate.tool.schema.internal.StandardTableExporter;
import org.hibernate.tool.schema.spi.Exporter;
import org.hibernate.type.StandardBasicTypes;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * 仅当ddl-auto: create-drop时，且数据库位阿里云数据库时使用，因为阿里云数据库drop时会因为CONSTRAINT而失败
 */
public class MySQL55DialectForAli extends MySQL55Dialect {

    private StandardTableExporter tableExporter = new StandardTableExporter(this) {
        @Override
        public String[] getSqlDropStrings(Table table, Metadata metadata) {
            List<String> list = new ArrayList<>();
            list.add("SET FOREIGN_KEY_CHECKS = 0");
            Arrays.stream(super.getSqlDropStrings(table, metadata)).forEach(sql -> list.add(sql));
            list.add("SET FOREIGN_KEY_CHECKS = 1");
            return list.toArray(new String[list.size()]);
        }
    };

    public MySQL55DialectForAli() {
        super();
        //注册全文搜索
        registerFunction("match", new SQLFunctionTemplate(StandardBasicTypes.DOUBLE, "match(?1) against  (?2)"));
        registerFunction("now", new SQLFunctionTemplate(StandardBasicTypes.DATE, "now()"));
        registerFunction("convert", new SQLFunctionTemplate(StandardBasicTypes.STRING, "convert(?1 using ?2)"));
        registerFunction("modulus", new SQLFunctionTemplate(StandardBasicTypes.INTEGER, "CRC32(?1)%?2"));
    }

    @Override
    public Exporter<Table> getTableExporter() {
        return tableExporter;
    }
}

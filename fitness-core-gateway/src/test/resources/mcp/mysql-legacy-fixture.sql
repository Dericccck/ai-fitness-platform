-- Minimal legacy-column fixture for isolated MySQL acceptance, never a production migration.
CREATE TABLE login_user (
 id VARCHAR(64) PRIMARY KEY, name VARCHAR(128), phone VARCHAR(64) UNIQUE,
 daily_votes INT DEFAULT 0, enabled BOOLEAN DEFAULT TRUE, version BIGINT DEFAULT 0
) ENGINE=InnoDB;
CREATE TABLE organization (
 id VARCHAR(64) PRIMARY KEY, name VARCHAR(128), priority INT DEFAULT 0, version BIGINT DEFAULT 0,
 address VARCHAR(255), summary TEXT, organization_type VARCHAR(64)
) ENGINE=InnoDB;
CREATE TABLE course (
 id VARCHAR(64) PRIMARY KEY, name VARCHAR(128), code VARCHAR(64), course_price INT, status INT,
 organization_id VARCHAR(64), create_time TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6),
 FOREIGN KEY (organization_id) REFERENCES organization(id)
) ENGINE=InnoDB;
CREATE TABLE login_user_authority (
 id VARCHAR(64) PRIMARY KEY, login_user_id VARCHAR(64), authority VARCHAR(64), entity_id VARCHAR(64),
 create_time TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6),
 FOREIGN KEY (login_user_id) REFERENCES login_user(id)
) ENGINE=InnoDB;
CREATE TABLE user_and_coach (
 id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64), coach_id VARCHAR(64), organization_id VARCHAR(64),
 status INT, deleted BOOLEAN DEFAULT FALSE, head_coach_ids VARCHAR(1000),
 FOREIGN KEY (user_id) REFERENCES login_user(id), FOREIGN KEY (coach_id) REFERENCES login_user(id),
 FOREIGN KEY (organization_id) REFERENCES organization(id)
) ENGINE=InnoDB;
CREATE TABLE contract (
 id VARCHAR(64) PRIMARY KEY, organization_id VARCHAR(64), user_id VARCHAR(64), course_id VARCHAR(64),
 contract_create_time DATE, contract_end_time DATE, remaining_class_hours INT, status INT,
 version BIGINT DEFAULT 0, deleted BOOLEAN DEFAULT FALSE, new_customer BOOLEAN DEFAULT FALSE,
 total_amount DECIMAL(18,2), refund_amount DECIMAL(18,2),
 create_time TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6),
 FOREIGN KEY (user_id) REFERENCES login_user(id), FOREIGN KEY (course_id) REFERENCES course(id),
 FOREIGN KEY (organization_id) REFERENCES organization(id)
) ENGINE=InnoDB;
CREATE TABLE appointment (
 id VARCHAR(64) PRIMARY KEY, status INT, course_name VARCHAR(128), course_id VARCHAR(64),
 course_start_date DATE, course_start_time DATETIME(6), course_end_time DATETIME(6),
 create_login_user_id VARCHAR(64), last_update_login_user_id VARCHAR(64), head_coach_ids VARCHAR(1000),
 user_id VARCHAR(64), coach_id VARCHAR(64), temp_coach_id VARCHAR(64), organization_id VARCHAR(64),
 contract_id VARCHAR(64), mark VARCHAR(1000), amount VARCHAR(64), deleted BOOLEAN DEFAULT FALSE,
 reamrk TEXT, create_time TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6),
 last_update_time TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6),
 KEY idx_coach_time (organization_id, coach_id, course_start_time),
 FOREIGN KEY (contract_id) REFERENCES contract(id), FOREIGN KEY (user_id) REFERENCES login_user(id),
 FOREIGN KEY (coach_id) REFERENCES login_user(id), FOREIGN KEY (organization_id) REFERENCES organization(id)
) ENGINE=InnoDB;
CREATE TABLE system_settings (organization_id VARCHAR(64), type VARCHAR(64), body TEXT) ENGINE=InnoDB;
CREATE TABLE vacation_record (
 organization_id VARCHAR(64), coach_id VARCHAR(64), status INT, start_date DATE, end_date DATE
) ENGINE=InnoDB;
INSERT INTO organization(id,name,organization_type) VALUES ('mysql-it-org','隔离验收门店','FITNESS');
INSERT INTO course(id,name,organization_id,status) VALUES ('mysql-it-course','隔离验收课程','mysql-it-org',1);

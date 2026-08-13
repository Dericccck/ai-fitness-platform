-- 不改写已发布的 V001；为创建草案增加请求级幂等约束。
ALTER TABLE training_plan
    ADD COLUMN create_request_id VARCHAR(128) NULL AFTER created_by;

ALTER TABLE training_plan
    ADD UNIQUE KEY uk_training_plan_create_request (create_request_id);

UPDATE training_plan
SET create_request_id = CONCAT('legacy-', id)
WHERE create_request_id IS NULL;

ALTER TABLE training_plan
    MODIFY create_request_id VARCHAR(128) NOT NULL;

INSERT INTO training_schema_version (version) VALUES ('V20260813_002');

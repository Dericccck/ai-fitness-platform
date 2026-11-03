-- Abort rather than delete or reinterpret any conflicting historical operation IDs.
-- The preflight query must return no rows before production migration:
-- SELECT request_id, COUNT(*) FROM training_confirmation_consumption GROUP BY request_id HAVING COUNT(*) > 1;
ALTER TABLE training_confirmation_consumption
    ADD UNIQUE KEY uk_training_confirmation_operation (request_id);
INSERT IGNORE INTO training_schema_version (version) VALUES ('V20261027_010');

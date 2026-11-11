USE dw_auto_stock;

DROP TRIGGER IF EXISTS trg_dim_energy_vehicle_ai;
CREATE TRIGGER trg_dim_energy_vehicle_ai
AFTER INSERT ON dim_energy_vehicle
FOR EACH ROW
UPDATE dim_model
SET energy_type = NEW.energy_type,
    vehicle_type = NEW.vehicle_type
WHERE model_name = NEW.model_name;

DROP TRIGGER IF EXISTS trg_dim_energy_vehicle_au;
CREATE TRIGGER trg_dim_energy_vehicle_au
AFTER UPDATE ON dim_energy_vehicle
FOR EACH ROW
UPDATE dim_model
SET energy_type = NEW.energy_type,
    vehicle_type = NEW.vehicle_type
WHERE model_name = NEW.model_name;

DROP TRIGGER IF EXISTS trg_dim_energy_vehicle_ad;
CREATE TRIGGER trg_dim_energy_vehicle_ad
AFTER DELETE ON dim_energy_vehicle
FOR EACH ROW
UPDATE dim_model
SET energy_type = '占位',
    vehicle_type = '占位'
WHERE model_name = OLD.model_name;

DROP TRIGGER IF EXISTS trg_dim_model_bi;
CREATE TRIGGER trg_dim_model_bi
BEFORE INSERT ON dim_model
FOR EACH ROW
SET NEW.energy_type = COALESCE(
        (SELECT dev.energy_type
         FROM dim_energy_vehicle dev
         WHERE dev.model_name = NEW.model_name
         LIMIT 1),
        '占位'
    ),
    NEW.vehicle_type = COALESCE(
        (SELECT dev.vehicle_type
         FROM dim_energy_vehicle dev
         WHERE dev.model_name = NEW.model_name
         LIMIT 1),
        '占位'
    );

DROP TRIGGER IF EXISTS trg_dim_model_bu;
CREATE TRIGGER trg_dim_model_bu
BEFORE UPDATE ON dim_model
FOR EACH ROW
SET NEW.energy_type = COALESCE(
        (SELECT dev.energy_type
         FROM dim_energy_vehicle dev
         WHERE dev.model_name = NEW.model_name
         LIMIT 1),
        '占位'
    ),
    NEW.vehicle_type = COALESCE(
        (SELECT dev.vehicle_type
         FROM dim_energy_vehicle dev
         WHERE dev.model_name = NEW.model_name
         LIMIT 1),
        '占位'
    );

-- 由 Python 刷新命令在创建会话临时表后执行，仅新增或更新匹配行。
INSERT INTO dim_model_ads_manual (
    model_name,
    ads_proportion_raw,
    ads_proportion_pct,
    ads_supplier,
    ads_supplier_mode,
    listed_at_raw,
    listed_at_date,
    listed_at_granularity,
    source_file
)
SELECT
    TRIM(model_name) AS model_name,
    TRIM(ads_proportion_raw) AS ads_proportion_raw,
    CAST(NULLIF(REPLACE(TRIM(ads_proportion_raw), '%', ''), '') AS DECIMAL(5,2)) AS ads_proportion_pct,
    TRIM(ads_supplier) AS ads_supplier,
    TRIM(ads_supplier_mode) AS ads_supplier_mode,
    TRIM(listed_at_raw) AS listed_at_raw,
    CASE
        WHEN TRIM(listed_at_raw) REGEXP '^[0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2}$'
            THEN STR_TO_DATE(REPLACE(TRIM(listed_at_raw), '-', '/'), '%Y/%c/%e')
        WHEN TRIM(listed_at_raw) REGEXP '^[A-Za-z]{3}-[0-9]{2}$'
            THEN STR_TO_DATE(CONCAT('01-', TRIM(listed_at_raw)), '%d-%b-%y')
        WHEN TRIM(listed_at_raw) REGEXP '^[0-9]{4}年[0-9]{1,2}月$'
            THEN STR_TO_DATE(
                CONCAT(
                    SUBSTRING_INDEX(TRIM(listed_at_raw), '年', 1),
                    '-',
                    LPAD(
                        SUBSTRING_INDEX(
                            SUBSTRING_INDEX(TRIM(listed_at_raw), '月', 1),
                            '年',
                            -1
                        ),
                        2,
                        '0'
                    ),
                    '-01'
                ),
                '%Y-%m-%d'
            )
        WHEN TRIM(listed_at_raw) REGEXP '^[0-9]{4}年[一二三四]季度$'
            THEN STR_TO_DATE(
                CONCAT(
                    SUBSTRING_INDEX(TRIM(listed_at_raw), '年', 1),
                    '-',
                    CASE SUBSTRING(TRIM(listed_at_raw), 6, 1)
                        WHEN '一' THEN '01'
                        WHEN '二' THEN '04'
                        WHEN '三' THEN '07'
                        WHEN '四' THEN '10'
                    END,
                    '-01'
                ),
                '%Y-%m-%d'
            )
        WHEN TRIM(listed_at_raw) REGEXP '^[0-9]{4}年[春夏秋冬]季$'
            THEN STR_TO_DATE(
                CONCAT(
                    SUBSTRING_INDEX(TRIM(listed_at_raw), '年', 1),
                    '-',
                    CASE SUBSTRING(TRIM(listed_at_raw), 6, 1)
                        WHEN '春' THEN '03'
                        WHEN '夏' THEN '06'
                        WHEN '秋' THEN '09'
                        WHEN '冬' THEN '12'
                    END,
                    '-01'
                ),
                '%Y-%m-%d'
            )
        ELSE NULL
    END AS listed_at_date,
    CASE
        WHEN TRIM(listed_at_raw) REGEXP '^[0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2}$' THEN 'day'
        WHEN TRIM(listed_at_raw) REGEXP '^[A-Za-z]{3}-[0-9]{2}$' THEN 'month'
        WHEN TRIM(listed_at_raw) REGEXP '^[0-9]{4}年[0-9]{1,2}月$' THEN 'month'
        WHEN TRIM(listed_at_raw) REGEXP '^[0-9]{4}年[一二三四]季度$' THEN 'quarter'
        WHEN TRIM(listed_at_raw) REGEXP '^[0-9]{4}年[春夏秋冬]季$' THEN 'season'
        ELSE 'unknown'
    END AS listed_at_granularity,
    COALESCE(NULLIF(TRIM(source_file), ''), 'data/manual_dimensions/dim_model_ads_matched.csv') AS source_file
FROM tmp_manual_dim_model_ads_manual
WHERE model_name IS NOT NULL
  AND TRIM(model_name) <> ''
ON DUPLICATE KEY UPDATE
    ads_proportion_raw = VALUES(ads_proportion_raw),
    ads_proportion_pct = VALUES(ads_proportion_pct),
    ads_supplier = VALUES(ads_supplier),
    ads_supplier_mode = VALUES(ads_supplier_mode),
    listed_at_raw = VALUES(listed_at_raw),
    listed_at_date = VALUES(listed_at_date),
    listed_at_granularity = VALUES(listed_at_granularity),
    source_file = VALUES(source_file);

CALL sp_refresh_dim_model_ads_all();

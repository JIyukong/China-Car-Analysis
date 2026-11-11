USE dw_auto_stock;

-- 只读视图：展示手工 ADS 表中未命中 dim_model 的车型清单。
-- 该视图不做任何写入，仅用于查询、审计和人工补维跟踪。

CREATE OR REPLACE VIEW vw_dim_model_ads_manual_missing_models AS
SELECT
    id,
    model_name,
    ads_proportion_raw,
    ads_proportion_pct,
    ads_supplier,
    ads_supplier_mode,
    listed_at_raw,
    listed_at_date,
    listed_at_granularity,
    matched_model_id,
    is_in_dim_model,
    missing_in_dim_model_reason,
    match_checked_at,
    source_file,
    created_at,
    updated_at
FROM dim_model_ads_manual
WHERE is_in_dim_model = 0;

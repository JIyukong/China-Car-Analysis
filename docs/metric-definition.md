# 指标口径定义

> 本文档定义所有业务指标的口径、计算公式、数据源和边界条件。

---

## 一、销量类指标

### 1. 月销量（sales_volume）

- **口径**：某车型在某自然月的全国上险/交强险数据量
- **数据源**：`ods_sales_monthly_model`（16888 口径）
- **粒度**：车型 × 月
- **注意**：16888 数据为月初公布，可能存在后续修正

### 2. 上市公司月度总销量（total_sales）

- **口径**：某上市公司旗下所有车型在某月销量之和
- **数据源**：`dwd_sales_monthly_model`（关联 `dim_model` → `dim_oem` → `dim_company`）
- **粒度**：上市公司 × 月
- **SQL 示例**：

```sql
SELECT
    dsm.stat_month,
    dc.company_name,
    SUM(dsm.sales_volume) AS total_sales
FROM dwd_sales_monthly_model dsm
JOIN dim_model dm ON dm.id = dsm.model_id
JOIN dim_oem doem ON doem.id = dm.oem_id
JOIN dim_company dc ON dc.id = doem.company_id
GROUP BY dsm.stat_month, dc.company_name
```

---

## 二、比率类指标

### 3. 市占率（market_share）

- **口径**：某主体销量 / 当月全市场总销量 × 100%
- **粒度**：可按公司/品牌/车型分别计算
- **SQL 示例**：

```sql
SELECT
    curr.stat_month,
    curr.company_name,
    curr.monthly_sales,
    ROUND(curr.monthly_sales / total.monthly_sales * 100, 4) AS market_share
FROM (
    SELECT stat_month, company_name, SUM(sales_volume) AS monthly_sales
    FROM dwd_sales_monthly_model
    GROUP BY stat_month, company_name
) curr
JOIN (
    SELECT stat_month, SUM(sales_volume) AS monthly_sales
    FROM dwd_sales_monthly_model
    GROUP BY stat_month
) total ON total.stat_month = curr.stat_month
```

### 4. 同比增长率（yoy_growth_rate）

- **口径**：(本月销量 - 去年同月销量) / 去年同月销量 × 100%
- **边界**：去年同月销量为 0 或 NULL 时，增长率记为 NULL
- **SQL 示例**：

```sql
SELECT
    curr.stat_month,
    curr.monthly_sales,
    prev.monthly_sales AS prev_year_sales,
    CASE
        WHEN prev.monthly_sales > 0
        THEN ROUND((curr.monthly_sales - prev.monthly_sales) / prev.monthly_sales * 100, 2)
        ELSE NULL
    END AS yoy_growth_rate
FROM monthly_agg curr
LEFT JOIN monthly_agg prev
    ON DATE_SUB(curr.stat_month, INTERVAL 12 MONTH) = prev.stat_month
```

### 5. 环比增长率（mom_growth_rate）

- **口径**：(本月销量 - 上月销量) / 上月销量 × 100%
- **边界**：上月销量为 0 或 NULL 时，增长率记为 NULL
- **SQL 示例**：

```sql
SELECT
    curr.stat_month,
    curr.monthly_sales,
    last_month.monthly_sales AS prev_month_sales,
    CASE
        WHEN last_month.monthly_sales > 0
        THEN ROUND((curr.monthly_sales - last_month.monthly_sales) / last_month.monthly_sales * 100, 2)
        ELSE NULL
    END AS mom_growth_rate
FROM monthly_agg curr
LEFT JOIN monthly_agg last_month
    ON DATE_SUB(curr.stat_month, INTERVAL 1 MONTH) = last_month.stat_month
```

---

## 三、股价类指标（M2 后启用）

### 6. 月度涨跌幅（monthly_return）

- **口径**：(月末收盘价 - 月初开盘价) / 月初开盘价 × 100%
- **数据源**：`ods_stock_daily`
- **粒度**：股票 × 月

### 7. 成交量加权均价（vwap）

- **口径**：SUM(成交额) / SUM(成交量)
- **数据源**：`ods_stock_daily`
- **粒度**：股票 × 日/周/月

---

## 四、关联分析指标（M5 后启用）

### 8. 销量-股价弹性系数

- **口径**：销量同比变化率 / 股价同期涨跌幅
- **用途**：衡量销量增长对股价的驱动程度
- **边界**：股价涨跌幅接近 0 时结果无意义，记为 NULL

### 9. 销量领先指标

- **口径**：比较月度销量同比变化率与下月股价涨跌幅的方向一致性
- **用途**：验证"销量先行、股价滞后"的投资逻辑

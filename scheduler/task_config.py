"""手动运行和定时任务共用的执行顺序与北京时间。"""

CHAINS = {
    "style": (
        "crawl_16888_style_monthly",
        "load_style_ods",
        "build_sales_dimensions",
        "run_dwd_clean",
        "update_lifecycle",
        "run_dws_agg",
    ),
    "city": ("crawl_16888_city_monthly", "load_city_ods", "run_dwd_city_clean"),
    "daily": ("load_stock_daily", "load_futures_daily", "load_index_daily", "load_stock_finance"),
}

MONTHLY_CRON = {
    "style": {"hour": 3, "minute": 0},
    "city": {"hour": 3, "minute": 30},
}

DAILY_TIMES = {
    "load_stock_daily": (23, 30),
    "load_futures_daily": (17, 30),
    "load_index_daily": (23, 45),
    "load_stock_finance": (18, 30),
}

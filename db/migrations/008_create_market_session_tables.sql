-- 管理员独立部署。SSE 沿用 dim_trade_calendar，其余市场按日期维护明确的开闭市。
CREATE TABLE IF NOT EXISTS etl_market_calendar (
    market VARCHAR(16) NOT NULL,
    trade_date DATE NOT NULL,
    is_open TINYINT(1) NOT NULL,
    source_ref VARCHAR(255) NOT NULL,
    PRIMARY KEY (market, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 仅记录已有交易所或正式公告证据的停牌。接口空返回不能自动认定为停牌。
CREATE TABLE IF NOT EXISTS etl_stock_suspension (
    stock_code VARCHAR(32) NOT NULL,
    trade_date DATE NOT NULL,
    source_ref VARCHAR(255) NOT NULL,
    PRIMARY KEY (stock_code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

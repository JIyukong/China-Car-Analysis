import pytest
from sqlalchemy import create_engine,text


def test_counties_idempotent_and_reject_conflicting_identity():
    from scheduler.county_areas import apply
    db=create_engine('sqlite://')
    with db.begin() as c:
        c.execute(text('CREATE TABLE dim_area (city_code TEXT PRIMARY KEY, city_name TEXT, city_short_name TEXT UNIQUE, city_level TEXT, province_code TEXT,province_name TEXT,province_short_name TEXT,province_type TEXT,region_code TEXT,region_name TEXT,city_tier TEXT,source_file TEXT)'))
        row=dict(city_code='659001',city_name='石河子市',city_short_name='石河子',city_level='县级市',province_code='650000',province_name='新疆维吾尔自治区',province_short_name='新疆',province_type='自治区',region_code='NW',region_name='西北',city_tier=None)
        assert apply(c,[row])['inserted']==1
        assert apply(c,[row])['inserted']==0
        with pytest.raises(ValueError): apply(c,[{**row,'province_code':'460000'}])

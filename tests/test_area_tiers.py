import pytest
from sqlalchemy import create_engine,text


def test_exact_tiers_preserve_unranked_and_reject_missing_city():
    from scheduler.area_tiers import apply
    from scheduler.yicai_tiers_2025 import classifications
    db=create_engine('sqlite://')
    with db.begin() as c:
        c.execute(text('CREATE TABLE dim_area(city_code TEXT PRIMARY KEY,city_name TEXT,city_short_name TEXT,city_tier TEXT)'))
        c.execute(text('INSERT INTO dim_area VALUES (:code,:name,:short,NULL)'),[{'code':'632500' if name=='海南' else str(700000+i),'name':'海南藏族自治州' if name=='海南' else name+'市','short':'海南州' if name=='海南' else name} for i,name in enumerate(classifications())])
        c.execute(text("INSERT INTO dim_area VALUES ('469002','琼海市','琼海',NULL)"))
        assert apply(c)['verified']==338
        assert c.execute(text("SELECT city_tier FROM dim_area WHERE city_code='469002'")).scalar() == '其他'
        assert c.execute(text("SELECT city_tier FROM dim_area WHERE city_code='632500'")).scalar() == '五线城市'
        assert apply(c)['updated']==0
        c.execute(text("DELETE FROM dim_area WHERE city_short_name='北京'"))
        with pytest.raises(ValueError): apply(c)

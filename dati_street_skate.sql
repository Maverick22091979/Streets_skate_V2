select count(*) from routes;
select provider, count(*) from routes group by provider;
select id, provider, name, start_date_local, distance_km, user_id
from routes
order by id desc;
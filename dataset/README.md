# Simulator dataset

There is no static dataset: the simulator generates data as it runs. To produce one:

1. Install Docker Desktop, then from the project root: `docker compose up -d`
2. Check: `curl http://localhost:8000/v1/health` (Swagger: http://localhost:8000/docs)
3. Export: `python dataset/export_dataset.py --ticks 500 --reset`

Output lands in `dataset/data/`. `demand_history.csv` is the forecasting series
(station_id, fuel_type, tick, sim_time, demand/served/unmet liters; 12 rows per tick).
Deterministic: baseline scenario, seed 12345 -> same data every run.
1 tick = 15 simulated minutes, so 500 ticks ~ 5.2 days, 2000 ticks ~ 21 days.

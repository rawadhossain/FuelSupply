# Run from dataset/ml:  cd dataset/ml && python ../benchmarks/forecast_benchmark.py
import pandas as pd, numpy as np, time, warnings; warnings.filterwarnings("ignore")
d=pd.read_csv('demand_full_features.csv'); TR=14*96; TE0=17*96
d['uid']=d.station_id+'|'+d.fuel_type
test=d[d.tick>=TE0].copy()
res={}
def score(name,pred,t=None):
    t=test if t is None else t
    e=np.abs(t.demand_liters.values-pred); res[name]=(e.mean(), (e/t.demand_liters.values).mean()*100)
# 1 profile (train days only), multi-step: forecast whole test horizon from day17 start
p=d[d.tick<TR].groupby(['uid','tick_of_day']).demand_liters.mean().rename('p')
score('profile (ours)', test.join(p,on=['uid','tick_of_day']).p.values)
# 2 seasonal naive: last observed day before test (day 16) repeated
last=d[(d.tick>=TE0-96)&(d.tick<TE0)].set_index(['uid','tick_of_day']).demand_liters.rename('sn')
score('seasonal naive (last day)', test.join(last,on=['uid','tick_of_day']).sn.values)
# 3 statsforecast MSTL / AutoETS with season 96, fit on ticks < TE0
from statsforecast import StatsForecast
from statsforecast.models import MSTL, SeasonalNaive, AutoETS
tr=d[d.tick<TE0][['uid','tick','demand_liters']].rename(columns={'uid':'unique_id','tick':'ds','demand_liters':'y'})
h=test.tick.max()-TE0+1
for name,m in [('statsforecast MSTL(96)',MSTL(season_length=96)),('statsforecast SeasonalNaive',SeasonalNaive(season_length=96))]:
    t0=time.time(); sf=StatsForecast(models=[m],freq=1,n_jobs=1); fc=sf.forecast(df=tr,h=h)
    col=[c for c in fc.columns if c not in('unique_id','ds')][0]
    fc=fc.rename(columns={'unique_id':'uid','ds':'tick'}); m2=test.merge(fc,on=['uid','tick'])
    score(name+f' [{time.time()-t0:.1f}s]',m2[col].values,m2)
# 4 LightGBM one-step on engineered features (easier task: uses lag_1)
import lightgbm as lgb
feats=['tick_of_day','day_of_week','lag_1','lag_2','lag_4','lag_96','roll_mean_4','roll_mean_96','demand_factor']
dd=d.dropna(subset=['lag_96','roll_mean_96']).copy(); dd['sid']=dd.uid.astype('category').cat.codes
X=feats+['sid']; trn=dd[dd.tick<TE0]; tst=dd[dd.tick>=TE0]
m=lgb.LGBMRegressor(n_estimators=300,learning_rate=0.05,verbose=-1).fit(trn[X],trn.demand_liters)
score('LightGBM 1-step (uses lag_1)',m.predict(tst[X]),tst)
tp=tst.join(p,on=['uid','tick_of_day']); score('profile 1-step same rows',tp.p.values,tst)
for k,(mae,mape) in res.items(): print(f'{k:40s} MAE {mae:6.2f}  MAPE {mape:5.2f}%')

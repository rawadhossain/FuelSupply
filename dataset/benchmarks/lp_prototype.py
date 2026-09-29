# Prototype rolling-horizon LP (24h) with SciPy HiGHS. Run from dataset/ml:  cd dataset/ml && python ../benchmarks/lp_prototype.py
# Uses scenario initial inventories from SPEC.md and the baked profile; not wired to live state yet.
import pandas as pd, numpy as np, time, json
from scipy.optimize import linprog
from scipy.sparse import lil_matrix
st=pd.read_csv('ref_stations.csv'); dp=pd.read_csv('ref_depots.csv'); rt=pd.read_csv('ref_routes.csv')
prof=pd.read_csv('baseline_demand_profile.csv'); arr=pd.read_csv('supply_arrivals.csv')
F=['DIESEL','PETROL','OCTANE']; S=sorted(st.id.unique()); D=sorted(dp.id.unique()); H=96; t0=100
# initial state = scenario start from SPEC
Sinv={('station-mirpur','DIESEL'):9000,('station-mirpur','PETROL'):9000,('station-mirpur','OCTANE'):5000,('station-tongi','DIESEL'):11000,('station-tongi','PETROL'):6000,('station-tongi','OCTANE'):3500,('station-karnaphuli','DIESEL'):8500,('station-karnaphuli','PETROL'):9500,('station-karnaphuli','OCTANE'):5200,('station-coxsbazar','DIESEL'):7500,('station-coxsbazar','PETROL'):7500,('station-coxsbazar','OCTANE'):4200}
Dinv={('depot-gazipur','DIESEL'):60000,('depot-gazipur','PETROL'):45000,('depot-gazipur','OCTANE'):26000,('depot-patiya','DIESEL'):55000,('depot-patiya','PETROL'):42000,('depot-patiya','OCTANE'):24000}
Scap={(r.id,r.fuel_type):r.capacity for r in st.itertuples()}; Dcap={(r.id,r.fuel_type):r.capacity for r in dp.itertuples()}
disp={r.id:r.dispatch_capacity_per_tick for r in dp.drop_duplicates('id').itertuples()}
dem={(r.station_id,r.fuel_type,r.tick_of_day):r.mean for r in prof.itertuples()}
R=list(rt.itertuples())
idx={};n=0
def v(*k):
    global n
    if k not in idx: idx[k]=n; n+=1
    return idx[k]
for t in range(H):
    for r in R:
        for f in F: v('x',r.id,f,t)
    for s in S:
        for f in F: v('srv',s,f,t); v('I',s,f,t)
    for d_ in D:
        for f in F: v('Dp',d_,f,t); v('ov',d_,f,t)
c=np.zeros(n); ub=[None]*n
for k,i in idx.items():
    if k[0]=='srv': c[i]=-1
    if k[0]=='ov': c[i]=0.1
    if k[0]=='srv': ub[i]=dem[(k[1],k[2],(t0+k[3])%96)]
    if k[0]=='x': ub[i]=[r for r in R if r.id==k[1]][0].max_shipment
    if k[0]=='I': ub[i]=Scap[(k[1],k[2])]
    if k[0]=='Dp': ub[i]=Dcap[(k[1],k[2])]
Aeq=lil_matrix((0,n)); rows=[];beq=[]
E=[];b=[]
def eq(coefs,rhs): E.append(coefs); b.append(rhs)
for t in range(H):
    for s in S:
        for f in F:
            co={v('I',s,f,t):1, v('srv',s,f,t):1}
            if t>0: co[v('I',s,f,t-1)]=co.get(v('I',s,f,t-1),0)-1
            for r in R:
                if r.destination_station_id==s and t-r.transit_ticks>=0: co[v('x',r.id,f,t-r.transit_ticks)]=-1
            eq(co, Sinv[(s,f)] if t==0 else 0)
    for d_ in D:
        for f in F:
            co={v('Dp',d_,f,t):1, v('ov',d_,f,t):1}
            if t>0: co[v('Dp',d_,f,t-1)]=-1
            for r in R:
                if r.source_depot_id==d_: co[v('x',r.id,f,t)]=1
            a=arr[(arr.depot_id==d_)&(arr.fuel_type==f)&(arr.planned_tick==t0+t)].quantity.sum()
            eq(co,(Dinv[(d_,f)] if t==0 else 0)+a)
Ui=[];ub_=[]
for t in range(H):
    for d_ in D:
        Ui.append({v('x',r.id,f,t):1 for r in R if r.source_depot_id==d_ for f in F}); ub_.append(disp[d_])
def mat(L):
    M=lil_matrix((len(L),n))
    for i,co in enumerate(L):
        for j,val in co.items(): M[i,j]=val
    return M.tocsr()
tt=time.time()
res=linprog(c,A_ub=mat(Ui),b_ub=ub_,A_eq=mat(E),b_eq=b,bounds=[(0,u) for u in ub],method='highs')
print(res.status,res.message[:40],'vars',n,'cons',len(E)+len(Ui),'solve s',round(time.time()-tt,3))
tot=sum(dem[(s,f,(t0+t)%96)] for s in S for f in F for t in range(H)); print('served',round(-res.fun),'of demand',round(tot))

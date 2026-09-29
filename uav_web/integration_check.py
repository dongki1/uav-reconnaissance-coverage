import json,urllib.request,urllib.parse,zipfile,io,time,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];base='http://127.0.0.1:8766'
def get(p):return json.load(urllib.request.urlopen(base+p))
def post(p,obj):
    return json.load(urllib.request.urlopen(urllib.request.Request(base+p,data=json.dumps(obj).encode(),headers={'Content-Type':'application/json'})))
if len(sys.argv)!=2:raise SystemExit('Usage: python uav_web/integration_check.py INPUT.zip')
with zipfile.ZipFile(Path(sys.argv[1])) as z:
    meta=json.loads(z.read('metadata.json'));meta['images']=meta['images'][:1];meta['count']=1
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as small:
        small.writestr('metadata.json',json.dumps(meta));name=meta['images'][0]['archivePath'];small.writestr(name,z.read(name))
req=urllib.request.Request(base+'/api/upload?mode=manual&name=integration-check.zip',data=b.getvalue(),headers={'Content-Type':'application/zip'})
ident=json.load(urllib.request.urlopen(req))['id']
for _ in range(120):
    j=get('/api/jobs/'+ident)
    if j['status'] not in ('queued','running'):break
    time.sleep(.5)
assert j['status']=='done',j
assert len(j['frames'])==1 and j['frames'][0]['people']==[]
endpoint='/api/jobs/'+ident+'/edit'
j=post(endpoint,dict(frame=1,people=[dict(pixel_4k=[2032,738],ground_proxy=False)],reviewed=False))
p=j['frames'][0]['people'][0];assert p['source']=='manual' and 'estimate' in p
lat=p['estimate']['lat'];p['pixel_4k']=[2032,778]
j=post(endpoint,dict(frame=1,people=[p],reviewed=True));assert j['frames'][0]['reviewed'];assert j['frames'][0]['people'][0]['estimate']['lat']!=lat
raw=urllib.request.urlopen(base+'/api/jobs/'+ident+'/download').read()
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert {'report.html','people.json','people.geojson','images/0001.jpg'}<=set(z.namelist())
    assert '/*SNAPSHOT*/null' not in z.read('report.html').decode()
    assert len(json.loads(z.read('people.geojson'))['features'])==1
    out=root/'uav_web'/'data'/'export-check';out.mkdir(exist_ok=True)
    for name in z.namelist():
        p=out/name;p.parent.mkdir(exist_ok=True);p.write_bytes(z.read(name))
j=post(endpoint,dict(frame=1,people=[],reviewed=True));assert not j['frames'][0]['people']
print('PASS: upload, import, add, move, reproject, review, export, delete; job',ident)

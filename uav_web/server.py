from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor
import argparse, copy, json, mimetypes, os, threading, uuid, zipfile, io
from core import DEFAULT_CAMERA, import_zip, locate, geojson

BASE=Path(__file__).resolve().parent; DATA=BASE/'data'; DATA.mkdir(exist_ok=True)
LOCK=threading.RLock(); POOL=ThreadPoolExecutor(max_workers=1); JOBS={}; DETECTOR=None
for p in DATA.glob('*/job.json'):
    try:
        j=json.loads(p.read_text(encoding='utf-8'))
        if j['status'] in ('queued','running'):j.update(status='interrupted',message='서버 중단: 새 작업으로 다시 업로드하세요.')
        JOBS[j['id']]=j
    except (ValueError,KeyError): pass

def save(j):
    p=DATA/j['id']/'job.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(j,ensure_ascii=False,allow_nan=False),encoding='utf-8');tmp.replace(p)

def run(job_id):
    global DETECTOR
    j=JOBS[job_id]
    try:
        with LOCK:j.update(status='running',message='ZIP 검사 중');save(j)
        frames=import_zip(DATA/job_id/'input.zip',DATA/job_id,j['camera'])
        with LOCK:j['frames']=frames;save(j)
        if j['mode']=='auto' and DETECTOR is None:
            from detector import Detector
            DETECTOR=Detector(BASE/'models'/'yolo11n.pt')
        for f in frames:
            if f['status']=='error':continue
            with LOCK:j['message']=f"사진 {f['index']}/{len(frames)} 처리 중";save(j)
            try:
                people=DETECTOR.detect(DATA/job_id/f['file'],j['confidence']) if j['mode']=='auto' else []
                with LOCK:
                    for p in people:p['id']=uuid.uuid4().hex[:12];locate(f,p,j['camera'])
                    f.update(people=people,status='ready');save(j)
            except Exception as e:
                with LOCK:f.update(status='error',error=str(e));save(j)
        with LOCK:
            errors=sum(f['status']=='error' for f in frames)
            j.update(status='done',message=f'처리 완료 · {len(frames)}장 · 오류 {errors}장 · 검토 후 확정하세요.');save(j)
    except Exception as e:
        with LOCK:j.update(status='error',message=str(e));save(j)

class Handler(BaseHTTPRequestHandler):
    def reply(self,value,status=200,kind='application/json; charset=utf-8'):
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode() if not isinstance(value,bytes) else value
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        path=urlparse(self.path).path
        try:
            if path=='/':return self.reply((BASE/'index.html').read_bytes(),kind='text/html; charset=utf-8')
            if path=='/api/jobs':
                with LOCK:out=[{k:j[k] for k in ('id','name','status','message')} for j in reversed(list(JOBS.values()))]
                return self.reply(out)
            parts=path.strip('/').split('/')
            if len(parts)>=3 and parts[:2]==['api','jobs']:
                ident=parts[2]
                with LOCK:j=copy.deepcopy(JOBS[ident])
                if len(parts)==3:return self.reply(j)
                if parts[3]=='geojson':return self.reply(geojson(j))
                if parts[3]=='download':
                    buf=io.BytesIO()
                    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
                        z.writestr('people.json',json.dumps(j,ensure_ascii=False));z.writestr('people.geojson',json.dumps(geojson(j),ensure_ascii=False))
                        html=(BASE/'index.html').read_text(encoding='utf-8').replace('/*SNAPSHOT*/null',json.dumps(j,ensure_ascii=False).replace('<','\\u003c'))
                        z.writestr('report.html',html)
                        for f in j['frames']:
                            if f.get('file'):z.write(DATA/ident/f['file'],f['file'])
                    return self.reply(buf.getvalue(),kind='application/zip')
            if len(parts)==4 and parts[0]=='assets' and parts[2]=='images':
                if parts[1] not in JOBS or not parts[3].endswith('.jpg'):raise KeyError()
                p=DATA/parts[1]/'images'/parts[3]
                if p.resolve().parent!=(DATA/parts[1]/'images').resolve():raise KeyError()
                return self.reply(p.read_bytes(),kind='image/jpeg')
            self.reply({'error':'없음'},404)
        except (KeyError,FileNotFoundError):self.reply({'error':'없음'},404)
    def do_POST(self):
        try:
            host=self.headers.get('Host','');origin=self.headers.get('Origin')
            if host not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}') or (origin and origin not in ('http://'+host,)):
                return self.reply({'error':'동일한 로컬 웹 페이지에서 요청하세요.'},403)
            length=int(self.headers.get('Content-Length','0'))
            path=urlparse(self.path).path
            if path=='/api/upload':
                if not 1<=length<=512*1024**2:raise ValueError('ZIP은 512MB 이하이어야 합니다.')
                q=parse_qs(urlparse(self.path).query);mode=q.get('mode',['auto'])[0]
                if mode not in ('auto','manual'):raise ValueError('모드 오류')
                camera=json.loads(q.get('camera',[json.dumps(DEFAULT_CAMERA)])[0]);confidence=float(q.get('confidence',['.25'])[0])
                if not .05<=confidence<=.95:raise ValueError('검출 임계값 범위 오류')
                ident=uuid.uuid4().hex;(DATA/ident).mkdir()
                with (DATA/ident/'input.zip').open('wb') as out:
                    remaining=length
                    while remaining:
                        chunk=self.rfile.read(min(1024**2,remaining))
                        if not chunk:raise ValueError('업로드 중단')
                        out.write(chunk);remaining-=len(chunk)
                j=dict(id=ident,name=q.get('name',['upload.zip'])[0][:200],status='queued',message='대기 중',frames=[],camera=camera,confidence=confidence,mode=mode)
                with LOCK:JOBS[ident]=j;save(j)
                POOL.submit(run,ident);return self.reply({'id':ident},202)
            if length>1024**2:raise ValueError('요청 크기 초과')
            body=json.loads(self.rfile.read(length))
            parts=path.strip('/').split('/')
            if len(parts)==4 and parts[:2]==['api','jobs'] and parts[3]=='edit':
                with LOCK:
                    j=JOBS[parts[2]]
                    if j['status'] not in ('done','interrupted','error'):raise ValueError('처리가 끝난 후 수정하세요.')
                    f=next(f for f in j['frames'] if f['index']==body['frame'])
                    if not f.get('file'):raise ValueError('이미지 오류 프레임')
                    people=body['people']
                    if not isinstance(people,list) or len(people)>1000:raise ValueError('인원 데이터 오류')
                    clean=[]
                    for p in people:
                        u,v=map(float,p['pixel_4k'])
                        if not 0<=u<f['width'] or not 0<=v<f['height']:raise ValueError('이미지 범위를 벗어난 픽셀')
                        old=next((a for a in f['people'] if a['id']==p.get('id')),None)
                        same=old and old['pixel_4k']==[u,v]
                        a=dict(id=old['id'] if old else uuid.uuid4().hex[:12],pixel_4k=[u,v],source=old['source'] if same else 'manual',ground_proxy=bool(p.get('ground_proxy',False)))
                        if same and 'confidence' in old:a['confidence']=old['confidence']
                        clean.append(locate(f,a,j['camera']))
                    f.update(people=clean,reviewed=bool(body.get('reviewed')),status='ready');f.pop('error',None);save(j)
                    return self.reply(j)
            raise ValueError('알 수 없는 요청')
        except (ValueError,KeyError,TypeError,StopIteration) as e:self.reply({'error':str(e)},400)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8766);args=parser.parse_args()
    print(f'http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()

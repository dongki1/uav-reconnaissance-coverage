"""정찰 범위 분석 로컬 웹 서버.

실행: python -m reconnaissance.web --port 8780 [--ffmpeg PATH --ffprobe PATH]
      [--host 127.0.0.1] [--allow-host 이름.ts.net] [--view-only]
원격(Tailscale) 접속: 기본은 127.0.0.1 에만 열고 `tailscale serve` 로 HTTPS 프록시하는 방식을 권장.
--allow-host 에 적은 호스트(예: 장비명.tailnet.ts.net, 100.x.y.z)에서 온 요청만 분석 실행을 허용한다.
- /                 분석 목록 + 새 분석 실행(쏘티 폴더 탐색) 화면
- /reports/<이름>/  기존 report.html (위성지도 위 추가 관측 필요 영역)
기존 analyze.py 를 그대로 subprocess 로 호출하며 분석 로직은 바꾸지 않는다.
"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import argparse, json, mimetypes, os, string, subprocess, sys, threading, time, uuid

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / 'analysis'
LOCK = threading.RLock()
JOBS = {}
RUN_LOCK = threading.Lock()
CFG = dict(ffmpeg='ffmpeg', ffprobe='ffprobe', view_only=False, hosts=set())


def safe_isfile(p):
    # 끊긴 네트워크 드라이브·권한 없는 폴더는 OSError 를 내므로 False 로 처리
    try:
        return p.is_file()
    except OSError:
        return False


def safe_isdir(p):
    try:
        return p.is_dir()
    except OSError:
        return False


def is_sortie(p):
    return safe_isfile(p / 'sortie-meta.json') and safe_isfile(p / 'telemetry.jsonl')


def output_for(source):
    return ANALYSIS / f'{source.parent.name}_{source.name}'


def list_analyses():
    out = []
    if not ANALYSIS.is_dir():
        return out
    for d in ANALYSIS.iterdir():
        rep = d / 'report.html'
        if not d.is_dir() or not rep.is_file():
            continue
        item = dict(name=d.name, url=f'/reports/{d.name}/report.html', updated=rep.stat().st_mtime)
        try:
            s = json.loads((d / 'summary.json').read_text(encoding='utf-8-sig'))
            for k in ('source', 'analysis_area_m2', 'observed_area_m2', 'quality_area_m2', 'unobserved_area_m2',
                      'poor_quality_area_m2', 'insufficient_revisits_area_m2', 'accepted_frames', 'grid_cell_m'):
                item[k] = s.get(k)
        except (OSError, ValueError):
            pass
        out.append(item)
    return sorted(out, key=lambda x: -x['updated'])


def browse(path):
    if not path:
        if os.name == 'nt':
            # os.path.exists 는 끊긴 네트워크 드라이브(예: Z:)에서도 예외 없이 False
            roots = [f'{c}:\\' for c in string.ascii_uppercase if os.path.exists(f'{c}:\\')]
        else:
            roots = ['/']
        recent = [a['source'] for a in list_analyses() if a.get('source')]
        return dict(path='', parent=None, sortie=False, dirs=[dict(name=r, path=r, sortie=False) for r in roots],
                    recent=list(dict.fromkeys(recent))[:10])
    try:
        p = Path(path).expanduser().resolve()
    except OSError as e:
        raise ValueError(f'경로를 열 수 없습니다: {path} ({e})')
    if not safe_isdir(p):
        raise ValueError('폴더가 아니거나 연결되지 않은 위치입니다: ' + str(p))
    dirs = []
    try:
        for c in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            try:
                if c.is_dir() and not c.name.startswith(('.', '$')):
                    dirs.append(dict(name=c.name, path=str(c), sortie=is_sortie(c)))
            except OSError:
                pass
    except OSError as e:
        raise ValueError(f'폴더를 읽을 수 없습니다: {p} ({e.strerror or e})')
    parent = str(p.parent) if p.parent != p else ''
    return dict(path=str(p), parent=parent, sortie=is_sortie(p), dirs=dirs[:500], recent=[])


def run_job(job_id):
    j = JOBS[job_id]
    with RUN_LOCK:
        with LOCK:
            j.update(status='running', started=time.time())
        cmd = [sys.executable, '-X', 'utf8', '-m', 'reconnaissance.analyze', j['source'], '--output', j['output'],
               '--gsd', str(j['gsd']), '--cell', str(j['cell']), '--ffmpeg', CFG['ffmpeg'], '--ffprobe', CFG['ffprobe']]
        if j.get('aoi'):
            cmd += ['--aoi', j['aoi']]
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        try:
            proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                    text=True, encoding='utf-8', errors='replace', bufsize=1)
            for line in proc.stdout:
                with LOCK:
                    j['log'].append(line.rstrip())
                    del j['log'][:-300]
            code = proc.wait()
            with LOCK:
                if code == 0 and (Path(j['output']) / 'report.html').is_file():
                    j.update(status='done', url=f"/reports/{Path(j['output']).name}/report.html")
                else:
                    j.update(status='error', message=f'분석 실패 (exit {code})')
        except Exception as e:  # noqa: BLE001 - 사용자에게 그대로 보고
            with LOCK:
                j.update(status='error', message=str(e))
        with LOCK:
            j['finished'] = time.time()


INDEX = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>정찰 범위 분석</title><style>
*{box-sizing:border-box}body{margin:0;background:#101a28;color:#e3eaf4;font:15px system-ui}main{max-width:1200px;margin:auto;padding:24px}
h1{margin:0 0 6px}h2{margin:28px 0 10px;font-size:18px}.muted{color:#a6b8cf}a{color:#7cbeff}
.box{background:#1c2c41;border-radius:10px;padding:16px}table{width:100%;border-collapse:collapse}td,th{padding:9px;text-align:left;border-bottom:1px solid #34445a;vertical-align:top}
th{color:#a6b8cf;font-weight:500}button{padding:8px 12px;background:#293c55;color:#fff;border:1px solid #4b6282;border-radius:6px;cursor:pointer}button:hover{background:#3b5374}
button.primary{background:#2f6fb3;border-color:#4a8bd0}button:disabled{opacity:.5;cursor:default}input{padding:8px;background:#101a28;color:#e3eaf4;border:1px solid #4b6282;border-radius:6px}
#path{width:100%}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0}#dirs{max-height:320px;overflow:auto;border:1px solid #34445a;border-radius:6px;margin-top:8px}
#dirs div{padding:6px 10px;cursor:pointer;border-bottom:1px solid #22334a}#dirs div:hover{background:#26394f}.tag{font-size:12px;background:#2e7d4f;padding:2px 6px;border-radius:4px;margin-left:6px}
pre{background:#0b1320;padding:10px;border-radius:6px;max-height:260px;overflow:auto;white-space:pre-wrap;font-size:12px}.err{color:#ff8a80}.ok{color:#8be28b}
</style><main>
<h1>정찰 범위 분석</h1><div class="muted">실제 위성지도 위에 관측 격자를 겹쳐 추가 관측이 필요한 영역과 다음 촬영 후보를 확인합니다.</div>
<h2>분석 결과</h2><div class="box"><table><thead><tr><th>결과</th><th>원본 쏘티</th><th>분석영역</th><th>미관측</th><th>품질 부족</th><th>반복 부족</th><th>수정</th></tr></thead><tbody id="list"><tr><td colspan="7" class="muted">불러오는 중</td></tr></tbody></table></div>
<div id="newBox"><h2>새 분석</h2><div class="box">
<div class="row"><input id="path" placeholder="쏘티 폴더 경로 (post-flight\uavN\YYYYMMDD_HHMMSS) - 붙여넣기 또는 아래에서 탐색"></div>
<div class="row"><button id="go">이동</button><button id="up">상위 폴더</button><button id="roots">드라이브</button><span id="state" class="muted"></span></div>
<div id="recent" class="row"></div><div id="dirs"></div>
<div class="row" style="margin-top:14px"><label>GSD 기준 (m/pixel) <input id="gsd" type="number" step="0.01" value="0.10" style="width:90px"></label>
<label>격자 (m) <input id="cell" type="number" step="0.5" value="5" style="width:80px"></label>
<label>정찰 경계 GeoJSON (선택) <input id="aoi" placeholder="C:\...\mission.geojson" style="width:320px"></label></div>
<div class="row"><button class="primary" id="run" disabled>분석 실행</button><span id="jobState" class="muted"></span></div>
<pre id="log" hidden></pre></div></div>
<p class="muted">평탄 지면·공칭 카메라 기반 추정입니다. 위성 배경은 Esri 타일을 사용하며 인터넷 연결이 필요합니다.</p>
</main><script>
const el=id=>document.getElementById(id),m2=v=>v==null?'-':(v/10000).toFixed(2)+' ha';
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(u,o){const r=await fetch(u,o);const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}
async function loadList(){const a=await api('/api/analyses');el('list').innerHTML=a.length?a.map(x=>`<tr><td><a href="${esc(x.url)}" target="_blank">${esc(x.name)}</a></td><td class="muted">${esc(x.source)}</td><td>${m2(x.analysis_area_m2)}</td><td>${m2(x.unobserved_area_m2)}</td><td>${m2(x.poor_quality_area_m2)}</td><td>${m2(x.insufficient_revisits_area_m2)}</td><td class="muted">${new Date(x.updated*1000).toLocaleString()}</td></tr>`).join(''):'<tr><td colspan="7" class="muted">아직 결과가 없습니다.</td></tr>'}
let cur={path:'',parent:null,sortie:false};
async function browse(p){try{cur=await api('/api/browse?path='+encodeURIComponent(p||''));el('path').value=cur.path;
el('state').innerHTML=cur.sortie?'<span class="ok">쏘티 폴더입니다 (sortie-meta.json, telemetry.jsonl 확인)</span>':(cur.path?'쏘티 폴더가 아닙니다. 하위 폴더를 선택하세요.':'');
el('run').disabled=!cur.sortie;
el('recent').innerHTML=(cur.recent||[]).length?'최근: '+cur.recent.map(r=>`<button data-p="${esc(r)}">${esc(r.split(/[\\/]/).slice(-2).join('\\'))}</button>`).join(''):'';
el('dirs').innerHTML=cur.dirs.map(d=>`<div data-p="${esc(d.path)}">📁 ${esc(d.name)}${d.sortie?'<span class="tag">쏘티</span>':''}</div>`).join('')||'<div class="muted">하위 폴더 없음</div>'}
catch(e){el('state').innerHTML=`<span class="err">${esc(e.message)}</span>`}}
el('dirs').onclick=e=>{const d=e.target.closest('[data-p]');if(d)browse(d.dataset.p)};
el('recent').onclick=e=>{const d=e.target.closest('[data-p]');if(d)browse(d.dataset.p)};
el('go').onclick=()=>browse(el('path').value.trim());el('path').onkeydown=e=>{if(e.key==='Enter')browse(el('path').value.trim())};
el('up').onclick=()=>{if(cur.parent!=null)browse(cur.parent)};el('roots').onclick=()=>browse('');
el('run').onclick=async()=>{el('run').disabled=true;el('log').hidden=false;el('log').textContent='';
try{const j=await api('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:cur.path,gsd:+el('gsd').value,cell:+el('cell').value,aoi:el('aoi').value.trim()})});poll(j.id)}
catch(e){el('jobState').innerHTML=`<span class="err">${esc(e.message)}</span>`;el('run').disabled=!cur.sortie}};
async function poll(id){const j=await api('/api/jobs/'+id);el('log').textContent=j.log.join('\n');el('log').scrollTop=1e9;
if(j.status==='done'){el('jobState').innerHTML=`<span class="ok">완료</span> · <a href="${esc(j.url)}" target="_blank">보고서 열기</a>`;el('run').disabled=!cur.sortie;loadList();window.open(j.url,'_blank');return}
if(j.status==='error'){el('jobState').innerHTML=`<span class="err">${esc(j.message)}</span>`;el('run').disabled=!cur.sortie;return}
el('jobState').textContent=j.status==='queued'?'대기 중':'분석 중 (처음에는 영상 프레임 추출로 수 분 소요)';setTimeout(()=>poll(id),1500)}
loadList();api('/api/config').then(c=>{if(c.view_only)el('newBox').hidden=true;else browse('')});
</script></html>'''


def host_allowed(value, port):
    """Host/Origin 이 로컬 또는 --allow-host 목록에 있는지 (DNS rebinding·CSRF 방지)."""
    value = (value or '').strip().lower().rstrip('.')
    name = value.rsplit(':', 1)[0] if value.count(':') == 1 else value
    local = {f'127.0.0.1:{port}', f'localhost:{port}'}
    return value in local or value in CFG['hosts'] or name in CFG['hosts']


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def reply(self, value, status=200, kind='application/json; charset=utf-8'):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        try:
            if path in ('/', '/index.html'):
                return self.reply(INDEX.encode('utf-8'), kind='text/html; charset=utf-8')
            if path == '/api/analyses':
                return self.reply(list_analyses())
            if path == '/api/config':
                return self.reply(dict(view_only=CFG['view_only']))
            if path == '/api/browse':
                if CFG['view_only']:
                    return self.reply({'error': '보기 전용 모드입니다.'}, 403)
                return self.reply(browse(parse_qs(url.query).get('path', [''])[0]))
            if path.startswith('/api/jobs/'):
                with LOCK:
                    return self.reply(dict(JOBS[path.split('/')[3]]))
            if path.startswith('/reports/'):
                parts = path.split('/', 3)
                if len(parts) < 4 or not parts[3]:
                    raise KeyError(path)
                base = (ANALYSIS / parts[2]).resolve()
                target = (base / parts[3]).resolve()
                if ANALYSIS.resolve() not in base.parents or base not in target.parents or not target.is_file():
                    raise KeyError(path)
                kind = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                if kind.startswith('text/') or target.suffix in ('.json', '.geojson'):
                    kind = ('application/json' if target.suffix in ('.json', '.geojson') else kind) + '; charset=utf-8'
                return self.reply(target.read_bytes(), kind=kind)
            self.reply({'error': '없음'}, 404)
        except (KeyError, IndexError, FileNotFoundError):
            self.reply({'error': '없음'}, 404)
        except (ValueError, OSError) as e:
            self.reply({'error': str(e)}, 400)

    def do_POST(self):
        try:
            if CFG['view_only']:
                return self.reply({'error': '보기 전용 모드입니다.'}, 403)
            if not host_allowed(self.headers.get('Host', ''), self.server.server_port) or (
                    self.headers.get('Origin') and not host_allowed(urlparse(self.headers['Origin']).netloc, self.server.server_port)):
                return self.reply({'error': '허용된 주소의 웹 페이지에서 요청하세요.'}, 403)
            if urlparse(self.path).path != '/api/run':
                raise ValueError('알 수 없는 요청')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 65536:
                raise ValueError('요청 크기 오류')
            body = json.loads(self.rfile.read(length))
            try:
                source = Path(str(body.get('source', ''))).expanduser().resolve()
            except OSError as e:
                raise ValueError(f'경로를 열 수 없습니다: {e}')
            if not safe_isdir(source) or not is_sortie(source):
                raise ValueError('쏘티 폴더가 아닙니다 (sortie-meta.json, telemetry.jsonl 필요)')
            gsd, cell = float(body.get('gsd', .10)), float(body.get('cell', 5))
            if not (0 < gsd <= 5 and 0 < cell <= 100):
                raise ValueError('GSD 또는 격자 값 범위 오류')
            aoi = str(body.get('aoi') or '').strip()
            if aoi and not Path(aoi).is_file():
                raise ValueError('정찰 경계 GeoJSON 파일이 없습니다: ' + aoi)
            out = output_for(source)
            with LOCK:
                if any(j['status'] in ('queued', 'running') for j in JOBS.values()):
                    raise ValueError('다른 분석이 진행 중입니다. 끝난 뒤 다시 실행하세요.')
                ident = uuid.uuid4().hex[:12]
                JOBS[ident] = dict(id=ident, source=str(source), output=str(out), gsd=gsd, cell=cell, aoi=aoi,
                                   status='queued', log=[], message='')
            threading.Thread(target=run_job, args=(ident,), daemon=True).start()
            self.reply({'id': ident}, 202)
        except (ValueError, TypeError, OSError) as e:
            self.reply({'error': str(e)}, 400)


def main():
    p = argparse.ArgumentParser(description='정찰 범위 분석 로컬 웹 서버')
    p.add_argument('--port', type=int, default=8780)
    p.add_argument('--ffmpeg', default='ffmpeg')
    p.add_argument('--ffprobe', default='ffprobe')
    p.add_argument('--host', default='127.0.0.1', help='바인딩 주소 (기본 127.0.0.1, 예: Tailscale IP 100.x.y.z)')
    p.add_argument('--allow-host', action='append', default=[], help='분석 실행을 허용할 추가 호스트명/IP (반복 가능)')
    p.add_argument('--view-only', action='store_true', help='폴더 탐색·분석 실행을 막고 보고서 보기만 허용')
    a = p.parse_args()
    hosts = {h.strip().lower().rstrip('.') for h in a.allow_host if h.strip()}
    if a.host not in ('127.0.0.1', 'localhost', '0.0.0.0', '::'):
        hosts.add(a.host.lower())
    CFG.update(ffmpeg=a.ffmpeg, ffprobe=a.ffprobe, view_only=a.view_only, hosts=hosts)
    ANALYSIS.mkdir(exist_ok=True)
    print(f'http://{a.host}:{a.port}' + ('  (보기 전용)' if a.view_only else ''), flush=True)
    if hosts:
        print('허용 호스트: ' + ', '.join(sorted(hosts)), flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()

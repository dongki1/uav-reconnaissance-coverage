"""ZIP ingestion and flat-ground geolocation, independent of web framework."""
from pathlib import Path, PurePosixPath
import hashlib, io, json, math, zipfile
import numpy as np
from PIL import Image

DEFAULT_CAMERA = dict(fx=2248., fy=2248., cx=1920., cy=1080., width=3840, height=2160)

def rotation(a, axis):
    c, s = math.cos(a), math.sin(a)
    return np.array([[[1,0,0],[0,c,-s],[0,s,c]], [[c,0,s],[0,1,0],[-s,0,c]], [[c,-s,0],[s,c,0],[0,0,1]]][axis])

def project(state, pixel, camera):
    """Project an image pixel onto a locally flat ground plane.

    Body angles are 3-2-1 Euler angles in radians.  The two-axis gimbal has
    yaw and pitch only; its angles are supplied in degrees by the KARI data.
    Camera and navigation axes are both treated as forward/right/down (FRD/NED).
    """
    keys = ['latitude','longitude','altitude_agl','roll','pitch','yaw','gimbal_pitch','gimbal_yaw']
    if any(not isinstance(state.get(k), (int,float)) or not math.isfinite(state[k]) for k in keys):
        raise ValueError('GPS·AGL·기체 자세·짐벌 각도 중 누락/비정상 값이 있습니다.')
    if not -90 < state['latitude'] < 90 or not -180 <= state['longitude'] <= 180:
        raise ValueError('GPS 범위 오류')
    if state['altitude_agl'] <= 0: raise ValueError('AGL은 0보다 커야 합니다.')
    if state.get('gimbal_zoom') not in (None, 1, 1.0):
        raise ValueError('줌 1 이외의 프레임은 해당 줌의 카메라 보정값이 필요합니다.')
    r,p,y = [state[k] for k in ('roll','pitch','yaw')]
    gp,gy = [math.radians(state['gimbal_'+k]) for k in ('pitch','yaw')]
    R = rotation(y,2) @ rotation(p,1) @ rotation(r,0) @ rotation(gy,2) @ rotation(gp,1)
    u,v=pixel; d=R @ np.array([1,(u-camera['cx'])/camera['fx'],(v-camera['cy'])/camera['fy']])
    if d[2] <= 1e-6: raise ValueError('선택한 광선이 전방 지면과 교차하지 않습니다.')
    n,e,h=d*state['altitude_agl']/d[2]
    phi=math.radians(state['latitude']); a=6378137.; es=6.6943799901413165e-3
    rn=a/math.sqrt(1-es*math.sin(phi)**2); rm=a*(1-es)/(1-es*math.sin(phi)**2)**1.5
    return dict(lat=state['latitude']+math.degrees(n/rm),lon=state['longitude']+math.degrees(e/(rn*math.cos(phi))),north_m=float(n),east_m=float(e))

def locate(frame, person, camera):
    person.pop('estimate',None);person.pop('alternative',None);person.pop('error',None);person.pop('model_shift_m',None)
    try:
        person['estimate']=project(frame['state'],person['pixel_4k'],camera)
    except ValueError as e: person['error']=str(e)
    return person

def import_zip(path, out, camera):
    out=Path(out);(out/'images').mkdir(parents=True,exist_ok=True)
    for k in DEFAULT_CAMERA:
        if not isinstance(camera.get(k),(int,float)) or not math.isfinite(camera[k]): raise ValueError('카메라 값 오류')
    if min(camera['fx'],camera['fy'],camera['width'],camera['height'])<=0: raise ValueError('초점거리/해상도 오류')
    with zipfile.ZipFile(path) as z:
        infos=z.infolist()
        if len(infos)>5000 or sum(i.file_size for i in infos)>3*1024**3: raise ValueError('ZIP 허용 크기/파일 수 초과')
        names={}
        for i in infos:
            p=PurePosixPath(i.filename.replace('\\','/'))
            if p.is_absolute() or '..' in p.parts or ':' in i.filename: raise ValueError('안전하지 않은 ZIP 경로')
            if i.filename in names: raise ValueError('중복 ZIP 경로')
            names[i.filename]=i
        candidates=[i for i in infos if PurePosixPath(i.filename).name=='metadata.json']
        if len(candidates)!=1: raise ValueError('metadata.json이 정확히 하나 필요합니다.')
        if candidates[0].file_size>20*1024**2: raise ValueError('메타데이터 크기 초과')
        meta=json.loads(z.read(candidates[0]).decode('utf-8-sig'))
        rows=meta.get('images')
        if not isinstance(rows,list) or not 1<=len(rows)<=500: raise ValueError('images 배열은 1~500장이 필요합니다.')
        frames=[]
        for idx,m in enumerate(rows,1):
            f=dict(index=idx,name=str(m.get('filename',idx)),time=str(m.get('timestamp','')),vehicle=str(m.get('vehicleName','')),state=m.get('uavStatus',{}),people=[],reviewed=False,status='pending',warnings=[])
            frames.append(f)
            try:
                wanted=m.get('archivePath','')
                matches=[i for i in infos if i.filename==wanted]
                if not matches:
                    base=PurePosixPath(wanted or m.get('filename','')).name
                    matches=[i for i in infos if PurePosixPath(i.filename).name==base]
                if len(matches)!=1: raise ValueError('이미지 파일 매칭 실패/중복')
                if matches[0].file_size>100*1024**2: raise ValueError('이미지 파일 크기 초과')
                raw=z.read(matches[0]);digest=hashlib.sha256(raw).hexdigest();f['hash']=digest
                if m.get('imageHash') and digest!=m['imageHash']: raise ValueError('SHA-256 불일치')
                if not m.get('imageHash'): f['warnings'].append('원본 해시 없음')
                im=Image.open(io.BytesIO(raw));im.load()
                if im.width!=camera['width'] or im.height!=camera['height']: raise ValueError('이미지 해상도가 카메라 설정과 다릅니다.')
                f['width'],f['height']=im.size;f['file']=f'images/{idx:04}.jpg'
                im.convert('RGB').save(out/f['file'],quality=95)
                if f['state'].get('gimbal_zoom') is None:f['warnings'].append('줌 미기록: 1 가정')
                if m.get('footprintClipped',0):f['warnings'].append('원본 footprint 경계 제한')
            except (ValueError,KeyError,OSError,zipfile.BadZipFile) as e: f.update(status='error',error=str(e))
        return frames

def geojson(job):
    return dict(type='FeatureCollection',features=[dict(type='Feature',geometry=dict(type='Point',coordinates=[p['estimate']['lon'],p['estimate']['lat']]),properties=dict(image=f['index'],id=p['id'],time=f['time'],source=p['source'],reviewed=f['reviewed'],ground_proxy=p.get('ground_proxy',False))) for f in job['frames'] for p in f['people'] if 'estimate' in p])

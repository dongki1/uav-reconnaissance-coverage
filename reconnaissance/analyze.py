"""Offline coverage quality maps; run with python -m reconnaissance.analyze."""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import subprocess

import numpy as np
from PIL import Image
from reconnaissance.geometry import rotation


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def extract_frames(video, out, interval, ffmpeg, ffprobe):
    """Use actual decoder PTS, never infer time from existing JPEG filenames."""
    out.mkdir(parents=True, exist_ok=True)
    probe = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,duration', '-of', 'json', str(video)], text=True))['streams'][0]
    identity = dict(path=str(video.resolve()), bytes=video.stat().st_size,
                    mtime_ns=video.stat().st_mtime_ns, interval=interval, version=1)
    manifest = out / 'manifest.json'
    if manifest.exists():
        cached = read_json(manifest)
        if cached['identity'] == identity and all((out / x['file']).exists() for x in cached['frames']):
            return cached, probe
    log = out / 'decode.log'
    vf = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval})',scale=1280:-2,showinfo"
    with log.open('w', encoding='utf-8') as stderr:
        subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-y', '-i', str(video), '-an',
            '-vf', vf, '-fps_mode', 'vfr', '-q:v', '3', str(out / 'sample-%06d.jpg')],
            stderr=stderr, stdout=subprocess.DEVNULL, check=True)
    times = re.findall(r'\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:([\d.eE+-]+)', log.read_text(encoding='utf-8'))
    if not times:
        raise ValueError('FFmpeg showinfo timestamps missing')
    data = dict(identity=identity, frames=[dict(file=f'sample-{i:06d}.jpg', seconds=float(t))
                                         for i, t in enumerate(times, 1)])
    if not all((out / x['file']).exists() for x in data['frames']):
        raise ValueError('Decoded frames and timestamps disagree')
    save_json(manifest, data)
    return data, probe


class LocalPlane:
    """WGS84 local tangent approximation (same convention as existing core.py)."""
    def __init__(self, lat, lon):
        self.lat, self.lon = lat, lon
        phi = math.radians(lat)
        d = 1 - 6.6943799901413165e-3 * math.sin(phi)**2
        self.nscale = math.pi / 180 * 6378137 * (1-6.6943799901413165e-3) / d**1.5
        self.escale = math.pi / 180 * 6378137 / math.sqrt(d) * math.cos(phi)

    def xy(self, lon, lat):
        return (np.asarray(lon)-self.lon)*self.escale, (np.asarray(lat)-self.lat)*self.nscale

    def lonlat(self, east, north):
        return np.stack([np.asarray(east)/self.escale+self.lon, np.asarray(north)/self.nscale+self.lat], axis=-1)


def attitude(row, mode='body'):
    g = row['gimbal']
    gp, gy = math.radians(g['pitch']), math.radians(g['yaw'])
    if mode == 'earth':
        return rotation(gy, 2) @ rotation(gp, 1)
    return (rotation(row['yaw'], 2) @ rotation(row['pitch'], 1) @ rotation(row['roll'], 0)
            @ rotation(gy, 2) @ rotation(gp, 1))


def validate_row(row):
    for k in ('lat', 'lon', 'altitudeAgl', 'roll', 'pitch', 'yaw'):
        if not isinstance(row.get(k), (float, int)) or not math.isfinite(row[k]):
            raise ValueError('missing/nonfinite ' + k)
    if not -90 < row['lat'] < 90 or not -180 <= row['lon'] <= 180:
        raise ValueError('GPS bounds')
    if row['altitudeAgl'] <= 2:
        raise ValueError('AGL <= 2 m')
    for k in ('pitch', 'yaw'):
        if not isinstance(row.get('gimbal', {}).get(k), (float, int)) or not math.isfinite(row['gimbal'][k]):
            raise ValueError('missing gimbal ' + k)
    if row['gimbal'].get('zoom') not in (None, 1, 1.0):
        raise ValueError('uncalibrated zoom')


def ground_to_image(east, north, height, R, camera):
    """Inverse projection and largest singular value of ground/pixel Jacobian."""
    q = np.column_stack([north, east, np.full_like(east, height)]) @ R
    depth = q[:, 0]
    safe = np.where(depth > 1e-8, depth, 1e-8)
    u = camera['cx'] + camera['fx']*q[:, 1]/safe
    v = camera['cy'] + camera['fy']*q[:, 2]/safe
    rays = np.column_stack([np.ones_like(u), (u-camera['cx'])/camera['fx'], (v-camera['cy'])/camera['fy']]) @ R.T
    z = np.maximum(rays[:, 2], 1e-10)
    jac = np.stack([height*(R[:2, 1][None, :]*z[:, None]-rays[:, :2]*R[2, 1]) / (camera['fx']*z[:, None]**2),
                    height*(R[:2, 2][None, :]*z[:, None]-rays[:, :2]*R[2, 2]) / (camera['fy']*z[:, None]**2)], axis=2)
    gsd = np.linalg.svd(jac, compute_uv=False)[:, 0]
    incidence = np.degrees(np.arctan2(np.hypot(east, north), height))
    valid = (depth > 0) & (u >= 0) & (u < camera['width']) & (v >= 0) & (v < camera['height'])
    return u, v, gsd, incidence, valid


def patch_quality(path):
    """8 x 6 local patches, variance of Laplacian and clipped-pixel fraction."""
    with Image.open(path) as im:
        a = np.asarray(im.convert('L').resize((1280, 720)), dtype=float)
    blur, clip = np.empty((6, 8)), np.empty((6, 8))
    for y in range(6):
        for x in range(8):
            p = a[y*120:(y+1)*120, x*160:(x+1)*160]
            lap = p[:-2, 1:-1]+p[2:, 1:-1]+p[1:-1, :-2]+p[1:-1, 2:]-4*p[1:-1, 1:-1]
            blur[y, x] = lap.var()
            clip[y, x] = np.mean((p <= 5) | (p >= 250))
    return blur, clip


def inside_ring(x, y, ring):
    result = np.zeros(len(x), dtype=bool)
    for a, b in zip(ring, ring[1:]+ring[:1]):
        if a[1] == b[1]:
            continue
        crossing = ((a[1] > y) != (b[1] > y)) & (x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0])
        result ^= crossing
    return result


def aoi_rings(path, plane):
    data = read_json(path)
    geometries = [f['geometry'] for f in data['features']] if data['type']=='FeatureCollection' else [data.get('geometry', data)]
    polygons = []
    for g in geometries:
        if g['type'] not in ('Polygon', 'MultiPolygon'):
            raise ValueError('AOI must contain only WGS84 Polygon/MultiPolygon')
        for polygon in ([g['coordinates']] if g['type']=='Polygon' else g['coordinates']):
            polygons.append([np.column_stack(plane.xy(*np.asarray(ring).T)).tolist() for ring in polygon])
    if not polygons:
        raise ValueError('Empty AOI')
    return polygons


def plan_views(x, y, deficit, cell, camera, altitude, max_gsd, count):
    """Greedy marginal weighted area, nadir candidates, no route feasibility claim."""
    if altitude/min(camera['fx'], camera['fy']) > max_gsd:
        raise ValueError('Proposed altitude fails requested GSD')
    half_e = altitude * min(camera['cx'], camera['width']-camera['cx'])/camera['fx']
    half_n = altitude * min(camera['cy'], camera['height']-camera['cy'])/camera['fy']
    # Camera yaw=0, pitch=-90: horizontal image axis is east.
    active = np.flatnonzero(deficit > 0)
    stride = max(1, int(20 / cell))
    candidates = active[::stride]
    if len(candidates) > 2500:
        candidates = candidates[::math.ceil(len(candidates)/2500)]
    remaining = deficit.copy()
    plans = []
    for _ in range(count):
        best = None
        for idx in candidates:
            mask = (np.abs(x-x[idx]) <= half_e) & (np.abs(y-y[idx]) <= half_n)
            score = float(remaining[mask].sum()*cell**2)
            if best is None or score > best[0]:
                best = score, idx, mask
        if best is None or best[0] <= 0:
            break
        score, idx, mask = best
        plans.append(dict(rank=len(plans)+1, east_m=float(x[idx]), north_m=float(y[idx]),
            altitude_agl_m=altitude, gimbal_pitch_deg=-90, camera_yaw_deg=0,
            weighted_gain_m2=round(score, 2), deficient_area_m2=float(np.sum(mask & (remaining>0))*cell**2),
            bounds=[float(x[idx]-half_e), float(y[idx]-half_n), float(x[idx]+half_e), float(y[idx]+half_n)]))
        remaining[mask] = 0  # Candidate union benefit only, not simulated completion of repeat views.
    return plans


def feature(geometry, properties):
    return dict(type='Feature', geometry=geometry, properties=properties)


def run(args):
    source, out = Path(args.source).resolve(), Path(args.output).resolve()
    if out == source or source in out.parents:
        raise ValueError('Use an output directory outside the input sortie')
    out.mkdir(parents=True, exist_ok=True)
    meta = read_json(source/'sortie-meta.json')
    segments = meta['sync']['segments']
    if len(segments) != 1:
        raise ValueError('This version requires one video segment; split multi-segment inputs explicitly')
    video = source/segments[0]['file']
    frames, probe = extract_frames(video, out/'frames', args.interval, args.ffmpeg, args.ffprobe)
    print(f"Decoded {len(frames['frames'])} timestamped frames", flush=True)
    cam = meta['camera']
    camera = dict(width=probe['width'], height=probe['height'], fx=float(cam['focalLengthPx']),
                  fy=float(cam['focalLengthPx']), cx=float(cam['principalPointX']), cy=float(cam['principalPointY']))
    if (probe['width'], probe['height']) != (cam['imageWidth'], cam['imageHeight']):
        raise ValueError('Recorded image resolution differs from intrinsics')
    rows = [json.loads(line) for line in (source/'telemetry.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    rows.sort(key=lambda r:r['t'])
    ts = np.array([r['t'] for r in rows])
    anchor = meta['sync'].get('videoStartMsCorrected', segments[0]['startMs'])
    accepted, rejected = [], []
    for f in frames['frames']:
        time_ms = anchor+f['seconds']*1000+args.time_offset_ms
        j = int(np.argmin(np.abs(ts-time_ms)))
        dt = float(abs(ts[j]-time_ms))
        try:
            if dt > args.max_sync_ms:
                raise ValueError('telemetry time gap')
            validate_row(rows[j])
            accepted.append(dict(f, row=rows[j], sync_delta_ms=dt, time_ms=time_ms))
        except ValueError as e:
            rejected.append(dict(f, reason=str(e), sync_delta_ms=dt))
    if not accepted:
        raise ValueError('No valid frames')
    plane = LocalPlane(accepted[0]['row']['lat'], accepted[0]['row']['lon'])
    centers = np.array([plane.xy(f['row']['lon'], f['row']['lat']) for f in accepted])
    polygons = aoi_rings(args.aoi, plane) if args.aoi else None
    # First bounded grid covers all camera positions + configured maximum ground range.
    if polygons:
        vertices = np.array([p for poly in polygons for ring in poly for p in ring])
        low, high = vertices.min(axis=0), vertices.max(axis=0)
    else:
        low, high = centers.min(axis=0)-args.max_range, centers.max(axis=0)+args.max_range
    low = np.floor(low/args.cell)*args.cell
    nx, ny = np.ceil((high-low)/args.cell).astype(int)
    if nx*ny > 1_000_000:
        raise ValueError('Grid exceeds 1 million cells: increase --cell or restrict --aoi')
    xx, yy = np.meshgrid(low[0]+(np.arange(nx)+.5)*args.cell, low[1]+(np.arange(ny)+.5)*args.cell)
    x, y = xx.ravel(), yy.ravel()
    domain = np.ones(len(x), dtype=bool)
    if polygons:
        domain[:] = False
        for poly in polygons:
            part = inside_ring(x, y, poly[0])
            for hole in poly[1:]:
                part &= ~inside_ring(x, y, hole)
            domain |= part
    n = len(x)
    observations, good, epochs = [np.zeros(n, dtype=np.int32) for _ in range(3)]
    last_good = np.full(n, -np.inf)
    bins = np.zeros(n, dtype=np.uint16)
    best_gsd = np.full(n, np.inf)
    best_frame = np.full(n, -1, dtype=int)
    best_quality_gsd = np.full(n, np.inf)
    image_records = []
    for i, (f, center) in enumerate(zip(accepted, centers)):
        row, R = f['row'], attitude(f['row'], args.gimbal_frame)
        east, north = x-center[0], y-center[1]
        ids = np.flatnonzero(domain & (east**2+north**2 <= args.max_range**2))
        u, v, gsd, incidence, visible = ground_to_image(east[ids], north[ids], row['altitudeAgl'], R, camera)
        visible &= incidence <= args.max_incidence
        ii = ids[visible]
        gsd = gsd[visible]
        blur, clip = patch_quality(out/'frames'/f['file'])
        px = np.minimum(7, (u[visible]/camera['width']*8).astype(int))
        py = np.minimum(5, (v[visible]/camera['height']*6).astype(int))
        passed = (gsd <= args.gsd) & (blur[py, px] >= args.min_sharpness) & (clip[py, px] <= args.max_clipped)
        qi = ii[passed]
        observations[ii] += 1
        improved = gsd < best_gsd[ii]
        best_gsd[ii[improved]] = gsd[improved]
        good[qi] += 1
        selected = gsd[passed] < best_quality_gsd[qi]
        best_frame[qi[selected]] = i
        best_quality_gsd[qi[selected]] = gsd[passed][selected]
        independent_time = f['seconds']-last_good[qi] >= args.revisit_seconds
        epochs[qi[independent_time]] += 1
        last_good[qi[independent_time]] = f['seconds']
        azimuth = (np.degrees(np.arctan2(-east[qi], -north[qi]))+360) % 360
        direction = (azimuth/45).astype(int)
        # Nadir has undefined azimuth: reserve one common ninth bin.
        direction[np.hypot(east[qi], north[qi]) < row['altitudeAgl']*math.tan(math.radians(10))] = 8
        bins[qi] |= np.left_shift(np.uint16(1), direction.astype(np.uint16))
        image_records.append(dict(index=i, file='frames/'+f['file'], seconds=f['seconds'],
            sync_delta_ms=f['sync_delta_ms'], latitude=row['lat'], longitude=row['lon'],
            agl_m=row['altitudeAgl'], zoom_assumed=row['gimbal'].get('zoom') is None,
            covered_grid_m2=len(ii)*args.cell**2, quality_grid_m2=len(qi)*args.cell**2,
            median_patch_sharpness=float(np.median(blur)), max_patch_clipped=float(clip.max())))
        if i % 50 == 0:
            print(f'Projected {i+1}/{len(accepted)} frames', flush=True)
    observed = observations > 0
    if not np.any(observed & domain):
        raise ValueError('No observed grid centers; check AOI, attitude and altitude')
    if not polygons:
        # Honest provisional denominator: bounding rectangle of observed cell centers.
        domain &= (x >= x[observed].min()) & (x <= x[observed].max()) & (y >= y[observed].min()) & (y <= y[observed].max())
    directions = np.array([int(v).bit_count() for v in bins])
    complete = (epochs >= args.min_epochs) & (directions >= args.min_directions)
    status = np.where(~observed, 0, np.where(good==0, 1, np.where(complete, 3, 2)))
    deficit = np.where(status==0, 1., np.where(status==1, .8, np.where(status==2, .4, 0.)))
    ids = np.flatnonzero(domain)
    plans = plan_views(x[ids], y[ids], deficit[ids], args.cell, camera, args.next_altitude, args.gsd, args.views)
    for p in plans:
        p['longitude'], p['latitude'] = plane.lonlat(p['east_m'], p['north_m']).tolist()
    area = lambda mask: int(np.count_nonzero(mask & domain))*args.cell**2
    summary = dict(source=str(source), grid_cell_m=args.cell, aoi_mode='provided' if polygons else 'provisional_observed_bbox',
        area_is_cell_center_approximation=True, analysis_area_m2=area(domain), observed_area_m2=area(observed),
        quality_area_m2=area(good>0), repeat_and_direction_area_m2=area(complete),
        unobserved_area_m2=area(~observed), poor_quality_area_m2=area(observed & (good==0)),
        insufficient_revisits_area_m2=area((good>0) & ~complete),
        observation_area_sum_m2=int(observations[domain].sum())*args.cell**2,
        sampled_frames=len(frames['frames']), accepted_frames=len(accepted), rejected_frames=len(rejected),
        video_duration_s=float(probe['duration']), parameters=vars(args), camera=camera,
        map_origin=dict(latitude=plane.lat, longitude=plane.lon, north_m_per_degree=plane.nscale, east_m_per_degree=plane.escale),
        sync_start_uncertainty_ms=meta['sync'].get('startUncertaintyMs'),
        unknown_zoom_frames=sum(f['zoom_assumed'] for f in image_records),
        warnings=['평탄 지면·공칭 카메라·미보정 렌즈/보어사이트 기반 추정입니다.',
                  'DSM이 없어 건물·수목에 의한 가림은 반영하지 않습니다.',
                  'AGL(local) 수직 기준 및 짐벌 body/earth 기준을 현장 검증해야 합니다.',
                  '시간 간격 관측 횟수는 통계적으로 독립인 관측 횟수나 탐지확률이 아닙니다.',
                  '블러/노출 임계값과 우선순위 가중치는 사용자 조정 휴리스틱입니다.',
                  '후보 좌표는 촬영 위치 제안이며 장애물·공역·배터리를 고려한 비행 경로가 아닙니다.'])
    if not polygons:
        summary['warnings'].append('AOI 미지정: 임시 관측 경계 내 비율이며 전체 임무 완료율이 아닙니다.')
    if summary['unknown_zoom_frames']:
        summary['warnings'].append(f"{summary['unknown_zoom_frames']}장의 줌이 미기록되어 zoom=1을 가정했습니다.")
    if summary['sync_start_uncertainty_ms'] is not None:
        summary['warnings'].append(f"영상 시작시각의 기록된 불확실성: {summary['sync_start_uncertainty_ms']} ms.")
    summary['observed_percent'] = 100*summary['observed_area_m2']/summary['analysis_area_m2']
    summary['quality_percent'] = 100*summary['quality_area_m2']/summary['analysis_area_m2']
    summary['rejection_reasons'] = {reason:sum(f['reason']==reason for f in rejected)
                                    for reason in sorted({f['reason'] for f in rejected})}
    summary['max_matched_telemetry_delta_ms'] = max(f['sync_delta_ms'] for f in accepted)
    summary['logged_boresight_consistency'] = {}
    for mode in ('body', 'earth'):
        residuals = []
        for f in accepted:
            row = f['row']; b = row.get('boresight')
            if not b or not all(isinstance(b.get(k), (int,float)) and math.isfinite(b[k]) for k in ('lat','lon')):
                continue
            ray = attitude(row, mode)[:, 0]
            if ray[2] <= 1e-6:
                continue
            north, east = row['altitudeAgl']*ray[:2]/ray[2]
            be, bn = LocalPlane(row['lat'], row['lon']).xy(b['lon'], b['lat'])
            residuals.append(float(np.hypot(east-be, north-bn)))
        if residuals:
            summary['logged_boresight_consistency'][mode] = dict(samples=len(residuals),
                median_m=float(np.median(residuals)), p95_m=float(np.percentile(residuals,95)))
    summary['logged_boresight_consistency']['interpretation'] = 'Internal log consistency only, not surveyed ground-truth accuracy.'
    records, features = [], []
    labels = ['unobserved', 'poor_quality', 'needs_revisit', 'criteria_met']
    for idx in ids:
        lon, lat = plane.lonlat(x[idx], y[idx]).tolist()
        r = dict(cell_id=int(idx), longitude=lon, latitude=lat, east_m=float(x[idx]), north_m=float(y[idx]),
            status=labels[status[idx]], observations=int(observations[idx]), quality_observations=int(good[idx]),
            time_separated_observations=int(epochs[idx]), direction_bins=int(directions[idx]),
            best_gsd_m=float(best_gsd[idx]) if observed[idx] else None, best_quality_frame=int(best_frame[idx]),
            priority=float(deficit[idx]))
        records.append(r)
        d=args.cell/2
        ring=plane.lonlat(np.array([x[idx]-d,x[idx]+d,x[idx]+d,x[idx]-d,x[idx]-d]),
                          np.array([y[idx]-d,y[idx]-d,y[idx]+d,y[idx]+d,y[idx]-d])).tolist()
        features.append(feature(dict(type='Polygon', coordinates=[ring]), r))
    with (out/'coverage.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer=csv.DictWriter(handle, fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    save_json(out/'coverage.geojson', dict(type='FeatureCollection', features=features))
    save_json(out/'next_views.geojson', dict(type='FeatureCollection', features=[feature(dict(type='Point',
        coordinates=[p['longitude'], p['latitude']]), p) for p in plans]))
    save_json(out/'next_views.json', plans)
    save_json(out/'frames.json', dict(accepted=image_records, rejected=rejected))
    save_json(out/'summary.json', summary)
    report_data=dict(summary=summary, cells=records, plans=plans, frames=image_records)
    template=Path(__file__).with_name('report.html').read_text(encoding='utf-8')
    (out/'report.html').write_text(template.replace('__DATA__', json.dumps(report_data, ensure_ascii=False, allow_nan=False).replace('</','<\\/')), encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k.endswith(('m2','percent','frames'))}, ensure_ascii=False, indent=2), flush=True)
    return summary


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source');p.add_argument('--output', required=True)
    p.add_argument('--aoi', help='WGS84 GeoJSON Polygon/MultiPolygon, optional')
    p.add_argument('--ffmpeg', default='ffmpeg');p.add_argument('--ffprobe', default='ffprobe')
    p.add_argument('--interval', type=float, default=2)
    p.add_argument('--cell', type=float, default=5)
    p.add_argument('--gsd', type=float, default=.10)
    p.add_argument('--max-range', type=float, default=300)
    p.add_argument('--max-incidence', type=float, default=75)
    p.add_argument('--max-sync-ms', type=float, default=250)
    p.add_argument('--time-offset-ms', type=float, default=0)
    p.add_argument('--gimbal-frame', choices=['body','earth'], default='body')
    p.add_argument('--min-sharpness', type=float, default=30)
    p.add_argument('--max-clipped', type=float, default=.5)
    p.add_argument('--revisit-seconds', type=float, default=5)
    p.add_argument('--min-epochs', type=int, default=2)
    p.add_argument('--min-directions', type=int, default=1)
    p.add_argument('--next-altitude', type=float, default=50)
    p.add_argument('--views', type=int, default=10)
    return p


def main():
    p=parser();args=p.parse_args()
    for key in ('interval','cell','gsd','max_range','revisit_seconds','next_altitude','min_epochs','views'):
        if not math.isfinite(getattr(args,key)) or getattr(args,key)<=0:
            p.error(key+' must be finite and positive')
    if not 0 < args.max_incidence < 90 or not 1 <= args.min_directions <= 9:
        p.error('invalid incidence/direction threshold')
    if not 0 <= args.max_clipped <= 1 or not math.isfinite(args.min_sharpness) or args.min_sharpness < 0:
        p.error('invalid image quality threshold')
    if not math.isfinite(args.time_offset_ms) or not math.isfinite(args.max_sync_ms) or args.max_sync_ms<0:
        p.error('invalid synchronization threshold')
    run(args)


if __name__=='__main__':
    main()

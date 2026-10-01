"""Camera rotations and locally flat WGS84 ground projection."""
import math
import numpy as np

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

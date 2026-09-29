from pathlib import Path
import urllib.request, hashlib
p=Path(__file__).parent/'models'/'yolo11n.pt';p.parent.mkdir(exist_ok=True)
EXPECTED='0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1'
if not p.exists():
    urllib.request.urlretrieve('https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt',p.with_suffix('.part'))
    if hashlib.sha256(p.with_suffix('.part').read_bytes()).hexdigest()!=EXPECTED:
        raise RuntimeError('모델 SHA-256 검증 실패')
    p.with_suffix('.part').replace(p)
if hashlib.sha256(p.read_bytes()).hexdigest()!=EXPECTED:raise RuntimeError('모델 SHA-256 검증 실패')
print('Model SHA256 verified:',EXPECTED)

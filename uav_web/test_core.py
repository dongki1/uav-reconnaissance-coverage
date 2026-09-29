import unittest, tempfile, zipfile, json, io, hashlib
from pathlib import Path
from PIL import Image
from core import project,import_zip,DEFAULT_CAMERA,locate

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.state=dict(latitude=37,longitude=126,altitude_agl=30,roll=0,pitch=0,yaw=0,gimbal_roll=0,gimbal_pitch=-90,gimbal_yaw=0,gimbal_zoom=1)
    def test_nadir_and_45(self):
        p=project(self.state,[1920,1080],DEFAULT_CAMERA);self.assertAlmostEqual(p['north_m'],0)
        self.state['gimbal_pitch']=-45;p=project(self.state,[1920,1080],DEFAULT_CAMERA);self.assertAlmostEqual(p['north_m'],30)
    def test_yaw_and_pixel(self):
        self.state.update(gimbal_pitch=-45,yaw=1.5707963267948966)
        p=project(self.state,[1920,1080],DEFAULT_CAMERA);self.assertAlmostEqual(p['east_m'],30)
    def test_invalid(self):
        for key,value in [('altitude_agl',None),('altitude_agl',-1),('latitude',float('nan')),('gimbal_zoom',2)]:
            s=self.state.copy();s[key]=value
            with self.assertRaises(ValueError):project(s,[1920,1080],DEFAULT_CAMERA)
        self.state['gimbal_pitch']=20
        with self.assertRaises(ValueError):project(self.state,[1920,1080],DEFAULT_CAMERA)
    def test_two_axis_gimbal_does_not_require_roll(self):
        self.state.pop('gimbal_roll')
        p=project(self.state,[1920,1080],DEFAULT_CAMERA)
        self.assertAlmostEqual(p['north_m'],0)
    def test_safe_zip_and_hash(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);buf=io.BytesIO();Image.new('RGB',(32,24)).save(buf,format='JPEG');raw=buf.getvalue();camera=dict(DEFAULT_CAMERA,width=32,height=24)
            for bad in [False,True]:
                with zipfile.ZipFile(p/'a.zip','w') as z:
                    z.writestr('images/a.jpg',raw);z.writestr('metadata.json',json.dumps({'images':[{'archivePath':'images/a.jpg','imageHash':'bad' if bad else hashlib.sha256(raw).hexdigest()}]}))
                f=import_zip(p/'a.zip',p/'out',camera)[0];self.assertEqual(f['status'],'error' if bad else 'pending')
            with zipfile.ZipFile(p/'a.zip','w') as z:z.writestr('../escape','no')
            with self.assertRaises(ValueError):import_zip(p/'a.zip',p/'out',camera)
    def test_matches_previous_103(self):
        base=Path(__file__).resolve().parents[1]/'analysis/kari-images'
        if not (base/'people.json').exists():self.skipTest('기존 결과 없음')
        data=json.loads((base/'people.json').read_text(encoding='utf-8'));meta=json.loads((base/'metadata.json').read_text(encoding='utf-8'))
        for f,m in zip(data['frames'],meta['images']):
            for p in f['people']:
                q=project(m['uavStatus'],p['pixel_4k'],DEFAULT_CAMERA)
                self.assertAlmostEqual(q['lat'],p['estimate']['lat'],places=10);self.assertAlmostEqual(q['lon'],p['estimate']['lon'],places=10)

if __name__=='__main__':unittest.main()

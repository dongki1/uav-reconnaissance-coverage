"""Tiled person detection. Fixed local model only, never deserialize uploads."""
from pathlib import Path
from PIL import Image
import os

def iou(a,b):
    x=max(0,min(a[2],b[2])-max(a[0],b[0]));y=max(0,min(a[3],b[3])-max(a[1],b[1]))
    intersection=x*y
    return intersection/max(1,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection)

class Detector:
    def __init__(self, weights):
        if not Path(weights).is_file():raise RuntimeError('자동 검출 모델이 없습니다. setup.ps1을 먼저 실행하세요.')
        config=Path(__file__).parent/'data'/'model-config';config.mkdir(exist_ok=True)
        os.environ['YOLO_CONFIG_DIR']=str(config.resolve())
        os.environ['MPLCONFIGDIR']=str(config.resolve())
        from ultralytics import YOLO
        self.model=YOLO(str(weights))
    def detect(self,path,confidence=.25):
        im=Image.open(path).convert('RGB');w,h=im.size;boxes=[];tile=1024;step=768
        xs=sorted(set(list(range(0,max(1,w-tile+1),step))+[max(0,w-tile)]))
        ys=sorted(set(list(range(0,max(1,h-tile+1),step))+[max(0,h-tile)]))
        for y in ys:
            for x in xs:
                result=self.model.predict(im.crop((x,y,min(w,x+tile),min(h,y+tile))),imgsz=1024,classes=[0],conf=confidence,verbose=False,device='cpu')[0]
                for box,score in zip(result.boxes.xyxy.tolist(),result.boxes.conf.tolist()):
                    a,b,c,d=box;boxes.append(([a+x,b+y,c+x,d+y],score))
        kept=[]
        for box,score in sorted(boxes,key=lambda item:-item[1]):
            if all(iou(box,prev[0])<.45 for prev in kept):kept.append((box,score))
        return [dict(pixel_4k=[(b[0]+b[2])/2,b[3]],bbox=b,confidence=c,source='automatic',ground_proxy=False) for b,c in kept]

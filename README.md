# UAV ZIP 사람 분포 웹 분석

## 쏘티 정찰 범위 분석 (추가 기능)

원본 MP4와 비행 메타데이터로 관측 면적·품질 부족·미관측 격자를 계산하고 다음 쏘티의 촬영 후보 좌표를 만듭니다. Python + NumPy + Pillow, PATH의 FFmpeg/FFprobe가 필요합니다. 기존 사람 검출 웹 서버와 별도로 실행합니다.

```powershell
python -m pip install -r reconnaissance/requirements.txt
python -m reconnaissance.analyze '쏘티 폴더 절대경로' --output analysis/coverage
# 정찰 경계가 있을 때 (WGS84 GeoJSON):
python -m reconnaissance.analyze '쏘티 폴더 절대경로' --output analysis/coverage_aoi --aoi mission.geojson --gsd 0.10 --cell 5
python -m unittest reconnaissance.test_analyze -v
```

이 PC에서는 `./run-reconnaissance.ps1 -Source '쏘티 폴더 절대경로' -Output '결과 폴더'`로 설치된 Python을 찾아 실행할 수도 있습니다. `-Aoi '경계.geojson' -Gsd 0.05 -Cell 2.5`처럼 기준을 변경할 수 있습니다.

결과의 `report.html`을 열면 격자별 수치, 대표 영상, 우선순위별 촬영 후보를 볼 수 있습니다. `coverage.csv`, `coverage.geojson`, `next_views.geojson`을 다른 GIS/분석 도구에 사용할 수 있습니다. 재실행 시 동일 영상·간격의 추출 프레임을 재사용합니다. 원본 쏘티 밖에 출력 폴더를 지정하세요.

보고서는 실제 위성지도 위에 관측 격자를 겹쳐 표시합니다. 기본으로 추가 관측 필요 영역만 보이며, 겹침 농도·확대·이동·촬영 후보의 위치 보기를 지원합니다. 위성 배경은 인터넷 연결이 필요하고 Esri에 해당 지역의 타일 좌표가 전송됩니다. 위성지도를 끄면 배경 없이 분석 결과를 확인할 수 있습니다.

기본값은 2초 표본, 5 m 격자, GSD 10 cm/pixel 이하, 시간 간격 관측 2회, 다음 촬영 AGL 50 m입니다. AOI 미지정 시 임시 관측 경계 내부만 평가합니다. 평탄 지면·공칭 카메라에 의한 추정으로 **전체 임무 완료율이나 식별 성공률이 아닙니다**. 촬영 후보는 실행 가능한 비행경로가 아닙니다. [논문 근거·수학·한계](docs/RECONNAISSANCE_METHOD.md)를 확인하세요.

브라우저에서 ZIP을 넣으면 모든 사진을 검토하고 사람 픽셀과 잠정 지리좌표를 관리합니다. 원본 ZIP은 서버 PC에서 처리하며 외부 AI 서비스로 업로드하지 않습니다. 로컬 단일 사용자용 서버입니다.

## 실행

현재 PC에는 `.venv` 환경이 설치되어 있습니다. PowerShell에서 프로젝트 폴더의 `./start.ps1`을 실행하고 **http://127.0.0.1:8766** 에 접속하세요. 처음 설치하는 PC에서는 Python 3.11 또는 3.12 설치 후 `./setup.ps1`을 먼저 실행합니다. 설치 때 패키지와 공식 모델 다운로드에 인터넷이 필요합니다.

1. ZIP 파일을 선택하고 카메라 값을 확인합니다.
2. 자동 검출 또는 수동 모드를 선택하고 분석 시작을 누릅니다.
3. 사진을 선택해 인원을 검토합니다. 확대 후 사람 추가, 번호 선택 후 픽셀 수정·삭제가 가능합니다.
4. 각 사진을 검토 완료로 표시합니다. 수정 시 좌표가 즉시 재계산되고 디스크에 저장됩니다.
5. 전체/사진별 지도 및 짐벌 해석 차이를 확인합니다. 보고서 ZIP은 압축을 풀어 `report.html`을 브라우저로 열 수 있는 읽기 전용 결과입니다. GeoJSON도 포함합니다.

위성지도는 Esri 타일을 사용하며 지역 타일 요청이 Esri로 전송됩니다. 위성지도 체크를 끄면 배경 없이 관측점을 볼 수 있습니다. 배경 영상의 촬영일은 UAV 촬영일과 다릅니다.

## ZIP 형식

`metadata.json` 한 개와 `images/*.jpg`를 포함하는 기존 KARI 내보내기 형식을 지원합니다. `metadata.json`의 `images` 배열 각 항목:

```json
{
  "archivePath": "images/001_example.jpg",
  "filename": "001_example.jpg",
  "imageHash": "SHA-256 (없으면 경고)",
  "timestamp": "2026-09-04T03:11:01.926Z",
  "vehicleName": "uav2",
  "uavStatus": {
    "latitude": 37.0225, "longitude": 126.5796,
    "altitude_agl": 31.5,
    "roll": 0.12, "pitch": 0.12, "yaw": 0.13,
    "gimbal_pitch": -45, "gimbal_yaw": 0.4,
    "gimbal_zoom": 1
  }
}
```

기체 각도는 rad, 짐벌은 deg, 고도는 로컬 AGL(m)입니다. 해상도는 업로드 설정과 같아야 합니다. 기본 공칭값은 3840×2160 / fx=fy=2248 / cx=1920, cy=1080입니다. 줌 미기록은 1로 가정합니다. 다른 줌은 좌표 계산을 보류합니다. AGL/자세가 없으면 사람은 표시할 수 있지만 좌표를 만들지 않습니다. 512MB ZIP, 압축해제 합계 3GB, 메타데이터 500장까지 제한합니다. 잘못된 프레임은 오류로 남기고 정상 프레임은 처리합니다.

## 검출과 추정

- YOLO11n COCO person을 CPU에서 1024px 타일, 256px 겹침으로 검출하고 IoU NMS를 적용합니다. 기본 임계값 0.25. 작은 사람, 앉은 사람, 가려진 사람은 누락될 수 있고 물체 오검출도 발생합니다.
- 자동 상자 하단 중앙을 발 접점으로 사용합니다. 수동 수정이 필요할 수 있습니다. 103명이라는 기존 결과는 수동 분석의 반복 관측 합계이며 자동 검출의 기대값이 아닙니다.
- Kim et al., Aerospace 2025, 12, 1065의 단일 광선/평면 교차. 칼만필터·프레임 간 추적·동일인 인식은 수행하지 않습니다.
- 2축 짐벌을 기체에 장착된 yaw-pitch 회전으로 모델링합니다. 회전 순서는 `Rz(기체 yaw) Ry(기체 pitch) Rx(기체 roll) Rz(짐벌 yaw) Ry(짐벌 pitch)`입니다. 렌즈 왜곡·지형·보어사이트·동기 오차는 미보정입니다.

수학적 모델, 코드 대응 관계와 논문 대비 구현 범위는 [`docs/TECHNICAL_METHOD.md`](docs/TECHNICAL_METHOD.md)에 정리되어 있습니다.

## 코드와 저장

- `uav_web/core.py`: 안전한 ZIP 읽기, 프레임 검사, 지리좌표 계산
- `uav_web/detector.py`: 타일 검출/NMS
- `uav_web/server.py`: 로컬 HTTP API, 순차 백그라운드 작업, 저장/내보내기
- `uav_web/index.html`: 업로드·사진 검토·수정·지도
- `uav_web/data/<작업ID>/`: 입력 ZIP, 이미지, `job.json` (서버 재시작 후 복원)

검증: `.venv/Scripts/python.exe -m unittest discover -s uav_web -p 'test_*.py'`

자동 검출 API: https://docs.ultralytics.com/modes/predict/
Ultralytics 배포/상용 사용은 해당 AGPL-3.0 및 Enterprise 라이선스 조건을 확인하세요.

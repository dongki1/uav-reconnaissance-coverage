"""Package an existing analysis as a small, portable, three-photo demo.

Usage: python -m reconnaissance.package_example analysis/SORTIE --output examples/dangjin
The numerical results are preserved; only three representative photos are bundled.
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from PIL import Image


def package(source, output):
    source, output = Path(source), Path(output)
    (output / 'frames').mkdir(parents=True, exist_ok=True)
    summary = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    summary['source'] = 'Example: Dangjin UAV7, 2026-09-04'
    for key in ('source', 'output', 'ffmpeg', 'ffprobe', 'aoi'):
        summary['parameters'].pop(key, None)
    frames = json.loads((source / 'frames.json').read_text(encoding='utf-8'))['accepted']
    coverage = json.loads((source / 'coverage.geojson').read_text(encoding='utf-8'))
    cells = [feature['properties'] for feature in coverage['features']]
    counts = Counter(c['best_quality_frame'] for c in cells if c['best_quality_frame'] >= 0)
    selected = [index for index, _ in counts.most_common(3)]
    demo_frames = [None] * len(frames)
    for index in selected:
        frame = dict(frames[index])
        frame['file'] = f'frames/example-{index:03d}.jpg'
        with Image.open(source / frames[index]['file']) as image:
            image = image.convert('RGB')
            image.thumbnail((960, 540))
            image.save(output / frame['file'], quality=82, optimize=True, exif=b'')
        demo_frames[index] = frame
    plans = json.loads((source / 'next_views.json').read_text(encoding='utf-8'))
    data = dict(summary=summary, cells=cells, plans=plans, frames=demo_frames)
    for name, value in [('summary.json', summary), ('coverage.geojson', coverage),
                        ('next_views.json', plans), ('frames.json', demo_frames)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for name in ('coverage.csv', 'next_views.geojson'):
        (output / name).write_bytes((source / name).read_bytes())
    template = Path(__file__).with_name('report.html').read_text(encoding='utf-8')
    gallery = '<div style="padding:16px"><b>실제 비행 예제 · 사진 3장만 수록</b><p>전체 분석 수치는 유지합니다. 사진이 없는 격자는 수치만 표시합니다. 사진은 960px로 축소했으며 원본 품질 재평가용이 아닙니다.</p>'
    for index in selected:
        frame = demo_frames[index]
        gallery += f'<figure style="display:inline-block;margin:8px"><a href="{frame["file"]}"><img src="{frame["file"]}" style="width:280px;max-width:100%" alt="비행 {frame["seconds"]:.1f}초 예제"></a><figcaption>{frame["seconds"]:.1f}초 · 프레임 {index}</figcaption></figure>'
    gallery += '</div>'
    template = template.replace('<main>', '<main>' + gallery)
    (output / 'report.html').write_text(template.replace('__DATA__', json.dumps(data, ensure_ascii=False, allow_nan=False).replace('</', '<\\/')), encoding='utf-8')
    print('Bundled representative frames:', selected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    package(args.source, args.output)

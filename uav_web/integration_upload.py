"""Integration utility: upload a ZIP through the same endpoint as the UI."""
import urllib.request, urllib.parse, json, sys
from pathlib import Path
path=Path(sys.argv[1]);q=urllib.parse.urlencode({'name':path.name,'mode':sys.argv[2] if len(sys.argv)>2 else 'auto'})
req=urllib.request.Request('http://127.0.0.1:8766/api/upload?'+q,data=path.read_bytes(),headers={'Content-Type':'application/zip'})
print(urllib.request.urlopen(req,timeout=120).read().decode())

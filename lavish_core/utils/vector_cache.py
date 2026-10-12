"""Data-only vector memory persistence; never deserialize executable objects."""
import json
from pathlib import Path

def read_memory(path):
    path=Path(path)
    if path.stat().st_size>20*1024*1024:raise ValueError('Memory cache exceeds limit')
    data=json.loads(path.read_text())
    if not isinstance(data,dict) or not isinstance(data.get('docs'),list) or not isinstance(data.get('meta'),list):raise ValueError('Invalid memory cache')
    if len(data['docs'])!=len(data['meta']) or not all(isinstance(t,str) for t in data['docs']) or not all(isinstance(m,dict) for m in data['meta']):raise ValueError('Invalid document metadata')
    return data

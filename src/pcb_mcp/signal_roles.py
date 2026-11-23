"""Numbered digital roles from server-owned source catalog; no client coordinates."""
import json
from pathlib import Path

def definitions():
 from .config import asset_root
 data=json.loads((asset_root()/'catalog/parts.json').read_text())
 return {p['symbol_id']:p for p in data['parts'] if p.get('signal_role')}

def roles(state):
 d=definitions()
 return [(c,d[c['library_id']]) for c in state['components'].values() if c['library_id'] in d]

def numbered_net(assigned,c,number):
 return next((assigned.get((c['id'],p['pin_id'])) for p in c['pins'] if p['number']==number),None)

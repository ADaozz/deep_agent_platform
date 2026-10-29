#!/usr/bin/env python3
"""Manage independent format features. Purge removes only the extension code/UI."""
import argparse
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from platform_app import format_plugins,domain

parser=argparse.ArgumentParser();parser.add_argument('action',choices=['list','enable','disable','uninstall']);parser.add_argument('feature',nargs='?',choices=['docx','pptx']);parser.add_argument('--purge',action='store_true');args=parser.parse_args()
if args.action=='list':print(json.dumps(format_plugins.status(),ensure_ascii=False,indent=2));raise SystemExit()
if not args.feature:parser.error('feature required')
meta=next((m for m in format_plugins.catalog() if m['id']==args.feature),None)
if meta is None:parser.error('Feature code is absent; reinstall its package before enabling')
if args.purge and args.action!='uninstall':parser.error('--purge is only valid with uninstall')
try:
 result=format_plugins.change(args.feature,args.action!='uninstall',args.action=='enable')
 if args.purge:
  shutil.rmtree(meta['directory'])
  ui=format_plugins.ROOT/'frontend'/'src'/'features'/args.feature
  if ui.exists():shutil.rmtree(ui)
 print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception as exc:
 print(getattr(exc,'detail',str(exc)),file=sys.stderr);raise SystemExit(1)

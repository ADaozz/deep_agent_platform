#!/usr/bin/env python3
"""Regenerate the verified manifest and a self-contained remote-image gallery."""
from collections import Counter
from pathlib import Path
import csv, json, html
BASE='https://raw.githubusercontent.com/acnlie/open-kimi-ppt-skill/main/'
HUB='https://github.com/acnlie/open-kimi-ppt-skill/blob/main/'
EXT={
'01_strategy':['dusk-violet-consulting','indigo-due-diligence','pine-green-strategy','red-black-business','moss-green-transformation','map-strategy'],
'02_business':['honey-orange-memo','lake-blue-memo','xuan-paper-annual','lead-gray-quarterly','ink-green-market-trends','orange-tech'],
'03_work':['sky-blue-wayfinding','mist-blue-travelogue','warm-jade-annual-report','color-stripes-documentary','moon-white-imagery','red-white-business'],
'04_promotion':['cream-collage','silk-yellow-magazine','pine-soot-pictorial','gold-orange-type-journal','silver-gray-luxury-magazine','fresh-brand'],
'05_academic':['pink-purple-diagnosis','teal-green-academic-defense','pastel-derivation','paper-white-courseware','blue-line-courseware','dark-themed-data']}
MAIN={
'consulting':['apricot-white-brief','indigo-due-diligence','marine-blue-research','moss-green-transformation','pine-green-strategy','red-black-growth'],
'finance':['black-gold-ledger','ebony-ledger','honey-orange-memo','lake-blue-memo','prospect-annual','rice-paper-annual'],
'work':['blue-flame-brand','electric-violet-business','moon-white-imagery','sky-blue-wayfinding','warm-clay-works','warm-jade-annual-report'],
'promotion':['aqua-charity-report','cream-collage','pine-soot-pictorial','silk-yellow-magazine','silver-gray-luxury-magazine','travel-green-handbook'],
'academic':['blue-line-courseware','deep-blue-atlas','paper-white-courseware','pastel-derivation','teal-green-academic-defense','wine-red-data']}
IMAGE_CATEGORIES={
'consulting':MAIN['consulting'],
'finance':MAIN['finance'],
'work':MAIN['work'],
'promotion':MAIN['promotion'],
'academic':MAIN['academic'],
'extra':['color-stripes-documentary','dark-themed-data','dusk-violet-consulting','fresh-brand','gold-orange-type-journal','ink-green-market-trends','lead-gray-quarterly','map-strategy','mist-blue-travelogue','orange-tech','pink-purple-diagnosis','red-black-business','red-white-business','xuan-paper-annual']}
LOOKUP={name:f'docs/themes/{cat}/{name}.jpg' for cat,names in IMAGE_CATEGORIES.items() for name in names}
assert len(LOOKUP)==44
rows=[]
for cat,names in EXT.items():
 for i,name in enumerate(names,1):
  p=f'skills/open-kimi-ppt/reference/design_system/{cat}/{i:02d}/en/{name}.md'
  rows.append((cat,name,p,'supplement'))
for cat,names in MAIN.items():
 for name in names:
  p=f'skills/open-kimi-ppt/reference/design_system/{cat}/{name}/design.md'
  rows.append((cat,name,p,'main'))
assert len(rows)==60
count=Counter(name for _,name,_,_ in rows)
manifest=[]
for cat,name,design,group in rows:
 img=LOOKUP.get(name)
 manifest.append({'category':cat,'theme_id':name,'kind':group,
  'design_repo_path':design,'reference_repo_path':img,
  'design_url':BASE+design,'reference_url':BASE+img if img else '',
  'design_page':HUB+design,'reference_page':HUB+img if img else '',
  'reference_status':'original' if img else 'generated_needed',
  'shared_with_another_design':count[name]>1,
  'output_path':f'{cat}/{name}'})
root=Path(__file__).resolve().parent
(root/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
fields=list(manifest[0].keys())
with (root/'manifest.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(manifest)
style='''<style>body{margin:0;background:#f6f6f3;color:#171717;font:14px/1.5 system-ui,Arial,sans-serif}.wrap{margin:auto;max-width:1350px;padding:32px 32px 80px}header{margin-bottom:25px}h1{font-size:27px;letter-spacing:-.04em;margin:0 0 6px}p{color:#666;margin:6px 0}.filters{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0}.filters button{background:white;border:1px solid #ddd;border-radius:20px;padding:8px 15px;cursor:pointer}.filters button.active{background:#222;color:white}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(255px,1fr));gap:15px}.tile{background:white;border:1px solid #e2e2e2;border-radius:9px;overflow:hidden}.thumb{height:200px;background:#eaeaea;display:flex;align-items:center;justify-content:center;overflow:hidden}.thumb img{max-width:100%;max-height:100%;object-fit:contain}.details{padding:12px 14px}.details b{display:block;font-size:14px}.meta{color:#777;font-size:12px;padding:3px 0 9px}a{color:#275fbe;text-decoration:none}a:hover{text-decoration:underline}.links{display:flex;gap:12px}code{font-size:12px}</style>'''
cards='\n'.join(f'''<article class="tile" data-category="{html.escape(d['category'])}"><div class="thumb"><a href="{d['reference_url']}" target="_blank"><img loading="lazy" src="{d['reference_url']}" alt="{html.escape(d['theme_id'])}"/></a></div><div class="details"><b>{html.escape(d['theme_id'])}</b><div class="meta">{html.escape(d['category'])} · {d['kind']} {'· 共享主题' if d['shared_with_another_design'] else ''}</div><div class="links"><a href="{d['design_page']}" target="_blank">design.md</a><a href="{d['reference_page']}" target="_blank">参考图</a></div></div></article>''' for d in manifest)
filterbuttons=''.join(f'<button data-filter="{c}">{c} ({sum(m["category"]==c for m in manifest)})</button>' for c in [*EXT,*MAIN])
page=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"/><title>Kimi Slides 设计规范与参考图 · 60 对</title>{style}</head><body><main class="wrap"><header><h1>Kimi Slides · 60 份设计规范 × 参考图</h1><p>44 张公开原图对应 60 个设计文件：其中 16 个设计主题在不同类别出现两次。参考图为远程 GitHub 图片，浏览需联网。</p><p>来源：<a href="https://github.com/acnlie/open-kimi-ppt-skill">acnlie/open-kimi-ppt-skill</a> · <a href="manifest.csv">CSV 映射</a> · <a href="manifest.json">JSON 映射</a></p></header><nav class="filters"><button class="active" data-filter="all">全部 (60)</button>{filterbuttons}</nav><section class="grid">{cards}</section></main><script>document.querySelectorAll('button[data-filter]').forEach(b=>b.addEventListener('click',()=>{{document.querySelectorAll('button').forEach(e=>e.classList.remove('active'));b.classList.add('active');const q=b.dataset.filter;document.querySelectorAll('article.tile').forEach(e=>e.hidden=q!=='all'&&e.dataset.category!==q)}}));</script></body></html>'''
(root/'gallery.html').write_text(page,encoding='utf-8')
print('Saved 60 rows, 44 unique images, 16 repeated IDs, 0 unmatched')

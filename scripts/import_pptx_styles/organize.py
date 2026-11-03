#!/usr/bin/env python3
"""Organize each Kimi Slides design-system Markdown with its JPG reference.

Sources: https://github.com/acnlie/open-kimi-ppt-skill/tree/main
Uses the checked-in manifest.json (60 design sources, 44 unique image sources).

Rules:
- Never rewrite the original design Markdown.
- Prefer original JPG for every matching design; duplicate the JPG into each folder.
- If an image is genuinely absent from the manifest, generate a distinctly labeled
  minimalist mock reference based on colors extracted from that Markdown.
- If a reference is over max_side, REMOVE its trailing 16:9 slide panels until both
  dimensions fit. Preserve pixels and scale of all retained content (no resizing).
- Log every source, crop, and failure; refuse to deliver noncompliant images.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageStat

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / 'manifest.json'
RAW_BASE = 'https://raw.githubusercontent.com/acnlie/open-kimi-ppt-skill/main/'
GIT_URL = 'https://github.com/acnlie/open-kimi-ppt-skill.git'
MAX_SIDE = 8192


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def acquire_repo(repo_root: Path | None, working_dir: Path, mode: str) -> Path | None:
    if repo_root:
        if not repo_root.is_dir():
            raise FileNotFoundError(f'--repo-root does not exist: {repo_root}')
        return repo_root
    if mode == 'raw':
        return None
    local = working_dir / 'open-kimi-ppt-skill'
    try:
        subprocess.run(['git','clone','--depth','1','--branch','main',GIT_URL,str(local)],check=True,timeout=180)
        return local
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as ex:
        print(f'[WARN] git clone failed ({ex}); retrying with raw GitHub URLs',file=sys.stderr)
        return None


def source_get(relative: str, repo: Path | None, cache: Path) -> Path:
    cached = cache / relative
    if cached.is_file(): return cached
    cached.parent.mkdir(parents=True,exist_ok=True)
    if repo:
        p = repo / relative
        if not p.is_file():
            raise FileNotFoundError(f'Source not present in local repository: {relative}')
        shutil.copy2(p,cached)
        return cached
    url = RAW_BASE + relative
    request = urllib.request.Request(url,headers={'User-Agent':'kimi-design-reference-organizer/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=30) as resp, cached.open('wb') as dest:
            shutil.copyfileobj(resp,dest)
    except Exception as e:
        cached.unlink(missing_ok=True)
        raise RuntimeError(f'Unable to download {url}: {e}') from e
    return cached


def font(size:int=40,bold:bool=False):
    options=(['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'] if bold else ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'])
    for p in options:
        try:return ImageFont.truetype(p,size)
        except OSError:continue
    return ImageFont.load_default()


def luminance(h: str) -> float:
    h=h.lstrip('#')
    if len(h)!=6:return .5
    r,g,b=[int(h[i:i+2],16)/255 for i in (0,2,4)]
    return .2126*r+.7152*g+.0722*b


def palette_from_markdown(txt:str) -> tuple[str,str,str,str]:
    # Preference: explicit label/value occurrences near '#HEX' tokens, then heuristics.
    found = re.findall(r'#[A-Fa-f0-9]{6}\b',txt)
    distinct=list(dict.fromkeys(x.upper() for x in found))
    # Dark and light surfaces; red if explicitly specified in the design source.
    background='#FFFFFF'
    for term in ['Background','background','canvas','paper']:
        m=re.search(r'(?i)'+re.escape(term)+r'[^\n#]{0,40}(#[0-9a-fA-F]{6})',txt)
        if m:
            background=m.group(1).upper();break
    if len(distinct)>0 and background=='#FFFFFF':
        light=[c for c in distinct if luminance(c)>.78]
        if light:background=light[0]
    dark = sorted((c for c in distinct if luminance(c)<.36),key=luminance)
    title=(dark[0] if dark else '#192129') if luminance(background)>.5 else '#FFFFFF'
    accent='#FF4D4D'
    for c in distinct:
        if c in (background,title,'#FFFFFF','#000000'):
            continue
        r,g,b=[int(c[i:i+2],16) for i in (1,3,5)]
        if max(r,g,b)-min(r,g,b)>45 and .16 < luminance(c) <.82:
            accent=c;break
    muted='#6D6D6D' if luminance(background)>.5 else '#A8B1BA'
    return background,title,accent,muted


def generated_reference(dest: Path, md_txt: str, theme_id: str, category: str) -> None:
    """Make a 4-slide, 1-column layout contact sheet from the actual design rules.

    This is deliberately marked synthetic and is NOT a replacement for actual
    Kimi theme preview artwork.
    """
    bg,fg,accent,muted=palette_from_markdown(md_txt)
    W,H=1440,810
    gutter=22
    sheet=Image.new('RGB',(W,4*H+3*gutter),(229,230,232))
    def sample_text(draw,xy,msg,size=31,color=None,bold=False,anchor=None):
        draw.text(xy,msg,font=font(size,bold),fill=color or fg,anchor=anchor)
    for n in range(4):
        canvas=Image.new('RGB',(W,H),bg)
        d=ImageDraw.Draw(canvas)
        d.line((72,72,1368,72),fill=muted,width=3)
        sample_text(d,(80,100),theme_id.replace('-',' ').title(),50,fg,True)
        sample_text(d,(80,167),['Executive overview','Data-driven evidence','Operating model','Action roadmap'][n],26,muted)
        d.rectangle((80,222,89,655),fill=accent)
        if n==0:
            sample_text(d,(126,260),'Strategy & evidence',66,fg,True)
            sample_text(d,(126,366),'Editable design • example reference',29,muted)
            for i,v in enumerate(['68%','4.2×','+27%']):
                xx=126+i*360
                d.rounded_rectangle((xx,530,xx+315,675),radius=5,fill=bg,outline=muted,width=2)
                sample_text(d,(xx+22,554),v,56,accent if i==1 else fg,True)
                sample_text(d,(xx+22,636),f'METRIC 0{i+1}',21,muted)
        elif n==1:
            sample_text(d,(132,252),'Performance by segment',38,fg,True)
            for i,val in enumerate([280,360,258,420,312,506]):
                xx=164+i*194; ybase=630
                d.rectangle((xx,ybase-val//2,xx+96,ybase),fill=accent if i==3 else muted)
                sample_text(d,(xx+48,ybase-val//2-48),str([45,52,40,68,50,79][i]),24,fg,True,anchor='mm')
                sample_text(d,(xx+50,655),f'S{i+1}',22,muted,False,anchor='mm')
            d.line((126,630,1334,630),fill=muted,width=2)
        elif n==2:
            sample_text(d,(132,252),'KPI architecture',38,fg,True)
            for i in range(3):
                x=136+i*396
                d.rectangle((x,342,x+340,585),outline=accent if i==1 else muted,width=3)
                sample_text(d,(x+26,379),['INPUT','PROCESS','OUTCOME'][i],25,muted,True)
                sample_text(d,(x+26,457),['120','86%','3.4×'][i],62,accent if i==1 else fg,True)
                if i<2:
                    d.line((x+345,465,x+380,465),fill=muted,width=4)
                    d.polygon([(x+381,465),(x+369,458),(x+369,472)],fill=muted)
        else:
            sample_text(d,(132,252),'Four-phase roadmap',38,fg,True)
            for i in range(4):
                x=138+i*306; top=570-i*64
                d.rectangle((x,top,x+244,659),fill=accent if i==3 else muted)
                sample_text(d,(x+24,top+18),f'{i+1:02d}',45,'#FFFFFF',True)
                sample_text(d,(x+10,684),['Assess','Prioritize','Implement','Scale'][i],24,fg)
        d.line((80,746,1360,746),fill=muted,width=2)
        sample_text(d,(80,754),'GENERATED REFERENCE • based on design.md',19,muted)
        sample_text(d,(1360,754),f'{n+1:02d} / 04',19,muted,anchor='ra')
        sheet.paste(canvas,(0,n*(H+gutter)))
    dest.parent.mkdir(parents=True,exist_ok=True)
    sheet.save(dest,quality=94,optimize=True)


def _scan_cut_boundary(image: Image.Image, axis: str, estimate: int, search:int) -> int:
    """Find a uniform, slide-separated band close to the estimated last page.

    This NEVER scales image content. If there is no obvious separator, uses
    the estimate, which is documented as heuristic in crop log.
    """
    extent=image.height if axis=='y' else image.width
    lo=max(1,estimate-search);hi=min(extent-1,estimate+search)
    if lo>=hi: return estimate
    # Sample the original only in 300px cross-axis to keep computation bounded.
    short=220
    if axis=='y':
        probe=image.convert('RGB').resize((short,max(1,round(extent*short/image.width))))
        scale=probe.height/extent
        cross_width=probe.width
    else:
        probe=image.convert('RGB').resize((max(1,round(extent*short/image.height)),short))
        scale=probe.width/extent
        cross_width=probe.height
    # Favor uniform gap bands AND boundaries close to predicted slide start.
    best=None
    for pos in range(lo,hi,max(1,int(5/scale))):
        small=max(0,min((probe.height if axis=='y' else probe.width)-1,int(pos*scale)))
        line=probe.crop((0,small,cross_width,small+1)) if axis=='y' else probe.crop((small,0,small+1,cross_width))
        std=sum(ImageStat.Stat(line).stddev)/3
        penalty=abs(pos-estimate)/max(1,search)*17
        score=std+penalty
        if best is None or score < best[0]:best=(score,pos)
    return best[1] if best else estimate


def trim_last_slide(image: Image.Image, axis: str, max_side: int) -> tuple[Image.Image,int,list[dict]]:
    """Remove panels from the END of a one-dimensional 16:9 contact strip.

    A complicated tiled montage cannot be reconstructed reliably from pixels
    alone. This algorithm assumes a single-column vertical or single-row
    horizontal strip, as in ordinary stacked PPT previews. A warning in the
    audit log makes the heuristic explicit.
    """
    removed=0
    ops=[]
    while max(image.size)>max_side:
        w,h=image.size
        vertical=axis=='y'
        extent=h if vertical else w
        across=w if vertical else h
        slide_span=across * (9/16 if vertical else 16/9)
        slide_n=max(2,round(extent/slide_span))
        # Estimate size from full sequence, preserving internal gutters.
        span=extent/slide_n
        if span < 40 or extent - span < 80:
            raise ValueError(f'Cannot remove a complete final PPT from image {w}x{h}: insufficient panels')
        estimate=round(extent-span)
        cut=_scan_cut_boundary(image,axis,estimate,round(min(span*.07,90)))
        if cut >= extent or cut<=0:
            raise ValueError(f'Invalid crop boundary {cut} for {w}x{h}')
        new=image.crop((0,0,w,cut)) if vertical else image.crop((0,0,cut,h))
        ops.append({'old_size':[w,h],'axis':axis,'predicted_panel_span':round(span,2), 'cut_position':cut,'new_size':list(new.size)})
        image=new
        removed+=1
        if removed>30:
            raise ValueError('Too many slides removed; not a standard contact strip')
    return image,removed,ops


def enforce_limit(src:Path,dest:Path,max_side:int) -> dict:
    with Image.open(src) as opened:
        original=[opened.width,opened.height]
        if max(original)<=max_side:
            shutil.copy2(src,dest)
            return {'original_size':original,'final_size':original,'removed_slides':0,'operations':[],'action':'kept_original_bytes'}
        img=opened.convert('RGB')
        removals=0
        records=[]
        while max(img.size)>max_side:
            w,h=img.size
            axis='y' if (h/max_side) >= (w/max_side) else 'x'
            img,k,ops=trim_last_slide(img,axis,max_side)
            removals+=k
            records.extend(ops)
        if max(img.size)>max_side:raise AssertionError('Image dimension limit still exceeded')
        img.save(dest,format='JPEG',quality=94,optimize=True)
        return {'original_size':original,'final_size':list(img.size),'removed_slides':removals,'operations':records,'action':'removed_trailing_slide_panels'}


def main():
    ap=argparse.ArgumentParser(description='Make 60 design.md + reference.jpg pairs. Source docs untouched; trim trailing slide panels if image >8192 px.')
    ap.add_argument('--repo-root',type=Path,help='Path to an existing cloned open-kimi-ppt-skill repository (offline mode)')
    ap.add_argument('--out',type=Path,default=Path('kimi-design-system-organized'))
    ap.add_argument('--method',choices=['clone','raw'],default='clone',help='When repo-root is not supplied, git clone (default) or download raw files')
    ap.add_argument('--max-side',type=int,default=MAX_SIDE)
    ap.add_argument('--limit',type=int,default=0,help='Optional small number of items for debugging only')
    args=ap.parse_args()
    if args.max_side<512:ap.error('--max-side must be >=512')
    records=json.loads(MANIFEST.read_text(encoding='utf8'))
    if args.limit:records=records[:args.limit]
    out=args.out.resolve()
    out.mkdir(parents=True,exist_ok=True)
    audit=[]
    errors=[]
    with tempfile.TemporaryDirectory(prefix='kimi-sources-') as td:
        td=Path(td)
        repo=acquire_repo(args.repo_root,td,args.method)
        cache=td/'cache'
        for i,item in enumerate(records,1):
            directory=out/item['output_path']
            directory.mkdir(parents=True,exist_ok=True)
            doc=directory/'design.md'
            jpg=directory/'reference.jpg'
            try:
                mdsrc=source_get(item['design_repo_path'],repo,cache)
                shutil.copy2(mdsrc,doc)
                if item['reference_repo_path']:
                    imgsrc=source_get(item['reference_repo_path'],repo,cache)
                    crop=enforce_limit(imgsrc,jpg,args.max_side)
                    provenance='original'
                else:
                    generated_reference(jpg,doc.read_text(encoding='utf-8'),item['theme_id'],item['category'])
                    crop=enforce_limit(jpg,jpg.with_name('.temp-reference.jpg'),args.max_side)
                    if crop['action']=='removed_trailing_slide_panels':
                        jpg.with_name('.temp-reference.jpg').replace(jpg)
                    else:
                        jpg.with_name('.temp-reference.jpg').unlink(missing_ok=True)
                    provenance='generated_from_design_md'
                with Image.open(jpg) as verified:
                    dims=[verified.width,verified.height]
                    if max(dims)>args.max_side:raise ValueError('Image still exceeds max-side')
                info={'category':item['category'],'theme_id':item['theme_id'],
                      'design_repo_path':item['design_repo_path'],'reference_repo_path':item['reference_repo_path'],
                      'provenance':provenance,'original_design_sha256':sha256(doc),
                      'reference_sha256':sha256(jpg),'reference_width':dims[0],
                      'reference_height':dims[1],**crop}
                (directory/'source.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
                audit.append(info)
                print(f'[{i:02d}/{len(records)}] OK {item["category"]}/{item["theme_id"]} {dims[0]}x{dims[1]}' + (f' - removed {crop["removed_slides"]} last PPT(s)' if crop['removed_slides'] else ''))
            except Exception as ex:
                error=f'{item["category"]}/{item["theme_id"]}: {ex}'
                errors.append(error)
                print(f'[{i:02d}/{len(records)}] ERROR {error}',file=sys.stderr)
    with (out/'audit.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['category','theme_id','design_repo_path','reference_repo_path','provenance','original_design_sha256','reference_sha256','reference_width','reference_height','removed_slides','action']
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(audit)
    (out/'audit.json').write_text(json.dumps({'completed':len(audit),'failed':len(errors),'max_side':args.max_side,'items':audit,'errors':errors},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (out/'FAILED.txt').write_text('\n'.join(errors)+'\n' if errors else 'No errors.\n',encoding='utf-8')
    # Fully local preview gallery (rendered images always obey max_side).
    gallery=(HERE/'gallery.html').read_text(encoding='utf-8')
    for item in records:
        folder=item['output_path']
        if item['reference_url']:
            gallery=gallery.replace(f'href="{item["reference_url"]}"',f'href="{folder}/reference.jpg"',1)
            gallery=gallery.replace(f'src="{item["reference_url"]}"',f'src="{folder}/reference.jpg"',1)
        gallery=gallery.replace(f'href="{item["design_page"]}"',f'href="{folder}/design.md"',1)
    (out/'index.html').write_text(gallery,encoding='utf-8')
    print(f'\nCompleted {len(audit)}/{len(records)}, failures {len(errors)}, crop events {sum(x["removed_slides"] for x in audit)}.')
    return 1 if errors else 0

if __name__=='__main__':sys.exit(main())

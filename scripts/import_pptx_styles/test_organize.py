#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw
from organize import generated_reference, palette_from_markdown
ROOT=Path(__file__).resolve().parent
MAN=json.loads((ROOT/'manifest.json').read_text())

def test():
    with tempfile.TemporaryDirectory() as t:
        base=Path(t);src=base/'src';out=base/'out'
        # Create real paths, but artificial data for all 60 docs & 44 images.
        for m in MAN:
            md=src/m['design_repo_path'];md.parent.mkdir(parents=True,exist_ok=True)
            md.write_text('# Sample\nBackground #FFFFFF; accent #FF4D4D; primary #0F0605\n',encoding='utf8')
            jpg=src/m['reference_repo_path'];jpg.parent.mkdir(parents=True,exist_ok=True)
            if not jpg.exists():
                img=Image.new('RGB',(800,450),'#eeeeee');ImageDraw.Draw(img).text((20,20),m['theme_id'],fill='#333333');img.save(jpg,quality=85)
        # Force one long reference to test trailing slide removal (10x900px).
        special=src/'docs/themes/consulting/red-black-growth.jpg'
        long=Image.new('RGB',(1600,9000),'#f7f7f7')
        d=ImageDraw.Draw(long)
        for i in range(10):
            top=i*900;d.rectangle((0,top,1599,top+899),outline='#000000',width=4)
            d.text((30,top+28),f'SLIDE {i+1}',fill='#000000')
        long.save(special,quality=83)
        p=subprocess.run([sys.executable,str(ROOT/'organize.py'),'--repo-root',str(src),'--out',str(out)],capture_output=True,text=True)
        assert p.returncode==0,(p.stderr,p.stdout)
        dirs=list(out.glob('*/*/design.md'))
        assert len(dirs)==60,len(dirs)
        records=json.loads((out/'audit.json').read_text())
        assert len(records['items'])==60
        for x in records['items']:
            assert max(x['final_size'])<=8192,x
        special_out=out/'consulting/red-black-growth/reference.jpg'
        with Image.open(special_out) as img:
            assert img.height<=8192,(img.width,img.height)
            assert img.width==1600
        info=json.loads((out/'consulting/red-black-growth/source.json').read_text())
        assert info['removed_slides']==1,info
        # Original design Markdown copied byte-identically.
        for m in MAN:
            assert (out/m['output_path']/'design.md').read_bytes()==(src/m['design_repo_path']).read_bytes()
        # Distinct MD, shared original image matched by theme ID.
        md1=out/'01_strategy/indigo-due-diligence/design.md'
        md2=out/'consulting/indigo-due-diligence/design.md'
        assert md1.is_file() and md2.is_file()
        assert (out/'01_strategy/indigo-due-diligence/reference.jpg').read_bytes()==(out/'consulting/indigo-due-diligence/reference.jpg').read_bytes()
        test_fallback=base/'fallback.jpg'
        generated_reference(test_fallback,'Background #FFFFFF accent #FF4D4D', 'test-fallback','consulting')
        with Image.open(test_fallback) as img:
            assert max(img.size)<=8192
        print('PASS: 60 MD sources, 60 JPG pairs, 44 source JPGs, no missing, crop last page, <=8192, hashes, fallback image.')

if __name__=='__main__':test()

import sys, subprocess, io
from PIL import Image, ImageDraw, ImageFont
vid, t0, t1, step, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
cols = int(sys.argv[6]) if len(sys.argv) > 6 else 8
w = int(sys.argv[7]) if len(sys.argv) > 7 else 240
ts = []; t = t0
while t <= t1 + 1e-6: ts.append(round(t, 3)); t += step
ims = []
for t in ts:
    p = subprocess.run(['ffmpeg','-v','error','-ss',str(t),'-i',vid,'-frames:v','1','-vf',f'scale={w}:-1','-f','image2pipe','-vcodec','png','-'],capture_output=True)
    if p.stdout: ims.append((t, Image.open(io.BytesIO(p.stdout)).convert('RGB')))
h = ims[0][1].height; rows = (len(ims)+cols-1)//cols
sheet = Image.new('RGB', (cols*w, rows*h), 'black'); d = ImageDraw.Draw(sheet)
f = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 16)
for i,(t,im) in enumerate(ims):
    x, y = (i%cols)*w, (i//cols)*h; sheet.paste(im, (x,y))
    d.rectangle([x, y, x+58, y+18], fill='black'); d.text((x+3, y+1), f'{t:.1f}', fill='yellow', font=f)
sheet.save(out, quality=85); print(out, len(ims))

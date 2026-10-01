
import sys,time,os,ctypes; sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from collections import Counter
from ctypes import wintypes
from ai_modeler import theme as TH, config as cfgmod
from ai_modeler import app as A
import shot
mode, part, out = sys.argv[1], sys.argv[2], sys.argv[3]
A.enable_dpi_awareness()
cfg=cfgmod.load(); cfg['theme']=mode; cfgmod.save(cfg); TH.set_mode(mode)
w=A.App(); w.geometry('+40+20'); w.update()
for _ in range(12): w.update(); time.sleep(0.05)
w.txt.delete('1.0','end'); w.txt.insert('1.0', part); w.generate()
t0=time.time()
while w._busy and time.time()-t0<25: w.update(); time.sleep(0.03)
for _ in range(40): w.update(); time.sleep(0.03)
top=ctypes.windll.user32.GetParent(w.winfo_id()) or w.winfo_id()
u32,g32=ctypes.windll.user32,ctypes.windll.gdi32
rc=wintypes.RECT(); u32.GetWindowRect(wintypes.HWND(top), ctypes.byref(rc))
W,H=rc.right-rc.left, rc.bottom-rc.top
hdc=u32.GetWindowDC(wintypes.HWND(top)); mdc=g32.CreateCompatibleDC(hdc)
bmp=g32.CreateCompatibleBitmap(hdc,W,H); g32.SelectObject(mdc,bmp)
u32.PrintWindow(wintypes.HWND(top), mdc, 2)
bi=shot._BMIH(); bi.biSize=ctypes.sizeof(shot._BMIH); bi.biWidth=W; bi.biHeight=-H
bi.biPlanes=1; bi.biBitCount=32; bi.biCompression=0
buf=ctypes.create_string_buffer(W*H*4); g32.GetDIBits(mdc,bmp,0,H,buf,ctypes.byref(bi),0)
shot._write_png(out, W, H, buf.raw)
d=buf.raw; st_=W*4; cnt=Counter()
for y in range(0,H,7):
    row=d[y*st_:(y+1)*st_]
    for x in range(0,W,7):
        i=x*4; cnt[(row[i+2],row[i+1],row[i])]+=1
tot=sum(cnt.values())
hx=lambda c:'#%02x%02x%02x'%c
print('%-6s %s  %dx%d  采样=%d  不同颜色=%d' % (mode, os.path.basename(out), W, H, tot, len(cnt)))
print('        主色: ' + '  '.join('%s %.1f%%'%(hx(c),100.0*n/tot) for c,n in cnt.most_common(5)))
print('        期望画布底色=%s  卡片底色=%s' % (TH.BG, TH.PANEL))
print('        状态栏: %s' % w.lbl_status.cget('text'))
w.on_close()

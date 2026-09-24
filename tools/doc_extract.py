# -*- coding: utf-8 -*-
"""Self-contained Word 97-2003 (.doc) text extractor: OLE2 (CFB) + FIB + piece table."""
import os, struct, sys, glob

def read_ole(path):
    data = open(path, 'rb').read()
    if data[:8] != b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        raise SystemExit('NOT_OLE2')
    ssz = 1 << struct.unpack_from('<H', data, 30)[0]
    msz = 1 << struct.unpack_from('<H', data, 32)[0]
    nfat = struct.unpack_from('<I', data, 44)[0]
    dirstart = struct.unpack_from('<I', data, 48)[0]
    cutoff = struct.unpack_from('<I', data, 56)[0]
    minifat_start = struct.unpack_from('<I', data, 60)[0]
    difat_start = struct.unpack_from('<I', data, 68)[0]
    ndifat = struct.unpack_from('<I', data, 72)[0]
    END = 0xFFFFFFFA

    def soff(sid):
        return 512 + sid * ssz

    difat = list(struct.unpack_from('<109I', data, 76))
    sid = difat_start
    guard = 0
    while sid < END and guard < ndifat + 8:
        guard += 1
        vals = struct.unpack_from('<%dI' % (ssz // 4), data, soff(sid))
        difat.extend(vals[:-1])
        sid = vals[-1]
    difat = [x for x in difat[:nfat] if x < END]

    fat = []
    for fsid in difat:
        fat.extend(struct.unpack_from('<%dI' % (ssz // 4), data, soff(fsid)))

    def chain(start):
        out, cur, seen = [], start, set()
        while cur < END and cur not in seen:
            seen.add(cur)
            out.append(cur)
            if cur >= len(fat):
                break
            cur = fat[cur]
        return out

    def read_big(start, size=None):
        buf = bytearray()
        for s in chain(start):
            buf += data[soff(s):soff(s) + ssz]
        return bytes(buf[:size]) if size is not None else bytes(buf)

    dirraw = read_big(dirstart)
    entries = []
    for i in range(0, len(dirraw) - 127, 128):
        e = dirraw[i:i + 128]
        nlen = struct.unpack_from('<H', e, 64)[0]
        nm = e[:max(0, nlen - 2)].decode('utf-16-le', 'ignore') if nlen >= 2 else ''
        entries.append({'name': nm, 'type': e[66],
                        'start': struct.unpack_from('<I', e, 116)[0],
                        'size': struct.unpack_from('<Q', e, 120)[0]})

    root = next((e for e in entries if e['type'] == 5), None)
    ministream = read_big(root['start'], root['size']) if root and root['size'] else b''
    minifat = []
    for s in chain(minifat_start):
        minifat.extend(struct.unpack_from('<%dI' % (ssz // 4), data, soff(s)))

    def read_mini(start, size):
        buf, cur, seen = bytearray(), start, set()
        while cur < END and cur not in seen:
            seen.add(cur)
            buf += ministream[cur * msz:(cur + 1) * msz]
            if cur >= len(minifat):
                break
            cur = minifat[cur]
        return bytes(buf[:size])

    def get(name):
        for e in entries:
            if e['name'] == name and e['type'] == 2:
                if e['size'] < cutoff:
                    return read_mini(e['start'], e['size'])
                return read_big(e['start'], e['size'])
        return None
    return get, [e['name'] for e in entries if e['type'] == 2]

CP1252_SPECIAL = {
    0x82: 0x201A, 0x83: 0x0192, 0x84: 0x201E, 0x85: 0x2026, 0x86: 0x2020,
    0x87: 0x2021, 0x88: 0x02C6, 0x89: 0x2030, 0x8A: 0x0160, 0x8B: 0x2039,
    0x8C: 0x0152, 0x8E: 0x017D, 0x91: 0x2018, 0x92: 0x2019, 0x93: 0x201C,
    0x94: 0x201D, 0x95: 0x2022, 0x96: 0x2013, 0x97: 0x2014, 0x98: 0x02DC,
    0x99: 0x2122, 0x9A: 0x0161, 0x9B: 0x203A, 0x9C: 0x0153, 0x9E: 0x017E,
    0x9F: 0x0178,
}

def decode_piece(raw, compressed):
    if not compressed:
        return raw.decode('utf-16-le', 'replace')
    # Heuristic: try cp936 (GBK) first for CJK docs, else cp1252
    try:
        t = raw.decode('cp936')
        bad = t.count('\ufffd')
    except Exception:
        bad = 10 ** 9
    t2 = raw.decode('cp1252', 'replace')
    t2 = ''.join(chr(CP1252_SPECIAL.get(ord(c), ord(c))) for c in t2)
    return t if bad == 0 else t2

def extract(path):
    get, names = read_ole(path)
    wd = get('WordDocument')
    if wd is None:
        raise SystemExit('NO_WORDDOCUMENT stream: ' + repr(names))
    flags = struct.unpack_from('<H', wd, 10)[0]
    which = '1Table' if (flags & 0x0200) else '0Table'
    tbl = get(which) or get('1Table') or get('0Table') or b''
    nfib = struct.unpack_from('<H', wd, 2)[0]
    fcClx, lcbClx = struct.unpack_from('<II', wd, 0x01A2)
    pieces = []
    if lcbClx and fcClx + lcbClx <= len(tbl):
        clx = tbl[fcClx:fcClx + lcbClx]
        i = 0
        pcd_data = None
        while i < len(clx):
            if clx[i] == 0x01:
                cb = struct.unpack_from('<H', clx, i + 1)[0]
                i += 3 + cb
            elif clx[i] == 0x02:
                lcb = struct.unpack_from('<I', clx, i + 1)[0]
                pcd_data = clx[i + 5:i + 5 + lcb]
                break
            else:
                break
        if pcd_data:
            n = (len(pcd_data) - 4) // 12
            cps = struct.unpack_from('<%dI' % (n + 1), pcd_data, 0)
            for k in range(n):
                off = 4 * (n + 1) + k * 8
                fcval = struct.unpack_from('<I', pcd_data, off + 2)[0]
                comp = bool(fcval & 0x40000000)
                fc = fcval & 0x3FFFFFFF
                if comp:
                    fc //= 2
                pieces.append((cps[k], cps[k + 1], fc, comp))
    if not pieces:
        fcMin, fcMac = struct.unpack_from('<II', wd, 0x0018)
        raw = wd[fcMin:fcMac]
        txt = raw.decode('utf-16-le', 'replace')
    else:
        chunks = []
        for cpS, cpE, fc, comp in pieces:
            ln = cpE - cpS
            raw = wd[fc:fc + (ln if comp else ln * 2)]
            chunks.append(decode_piece(raw, comp))
        txt = ''.join(chunks)

    out = []
    for ch in txt:
        o = ord(ch)
        if o == 0x0D:
            out.append('\n')
        elif o == 0x07:
            out.append('\t')
        elif o in (0x0B, 0x0C):
            out.append('\n')
        elif o in (0x13, 0x14, 0x15, 0x01, 0x02, 0x08):
            out.append('')
        elif o == 0x1E:
            out.append('-')
        elif o == 0x1F:
            out.append('')
        elif o == 0xA0:
            out.append(' ')
        else:
            out.append(ch)
    return '\n'.join(l.rstrip() for l in ''.join(out).split('\n')), nfib, which, len(pieces)

if __name__ == '__main__':
    base = os.path.dirname(os.path.abspath(__file__))
    docs = [p for p in glob.glob(os.path.join(base, '*.doc')) if not os.path.basename(p).startswith('~$')]
    if not docs:
        raise SystemExit('NO_DOC')
    src = docs[0]
    text, nfib, which, npieces = extract(src)
    od = os.path.join(base, '_extract')
    os.makedirs(od, exist_ok=True)
    dst = os.path.join(od, 'doc.txt')
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(text)
    lines = text.split('\n')
    print('SRC=%s' % os.path.basename(src))
    print('NFIB=%d TABLE=%s PIECES=%d CHARS=%d LINES=%d' % (nfib, which, npieces, len(text), len(lines)))
    print('OUTBYTES=%d' % os.path.getsize(dst))
    print('--- HEAD 40 LINES ---')
    for l in lines[:40]:
        print(l[:160])

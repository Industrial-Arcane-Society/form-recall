"""Read PNG/DIB image data from the Windows clipboard without extra packages."""
from __future__ import annotations

import ctypes
import os
import struct
import tempfile
import uuid
import zlib
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CF_DIB = 8
CF_DIBV5 = 17
MAX_CLIPBOARD_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000


class ClipboardImageError(RuntimeError):
    """An image was present on the clipboard but could not be decoded."""


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)


def _channel(pixel: int, mask: int) -> int:
    if not mask:
        return 0
    shift = (mask & -mask).bit_length() - 1
    maximum = mask >> shift
    value = (pixel & mask) >> shift
    return (value * 255 + maximum // 2) // maximum


def dib_to_png(dib: bytes) -> bytes:
    """Convert common uncompressed 16/24/32-bit CF_DIB data into RGB PNG."""
    if len(dib) < 40:
        raise ClipboardImageError("剪贴板位图头数据不完整。")
    header_size, width, signed_height, planes, bit_count, compression = struct.unpack_from(
        "<IiiHHI", dib, 0)
    if header_size < 40 or header_size > len(dib):
        raise ClipboardImageError("剪贴板位图使用了不支持的 DIB 头。")
    if width <= 0 or signed_height == 0 or planes != 1:
        raise ClipboardImageError("剪贴板位图尺寸或平面数无效。")
    height = abs(signed_height)
    if width * height > MAX_IMAGE_PIXELS:
        raise ClipboardImageError("剪贴板图片分辨率过大。")
    if bit_count not in {16, 24, 32}:
        raise ClipboardImageError("剪贴板位图需为 16、24 或 32 位格式。")
    if compression not in {0, 3, 6}:  # BI_RGB, BI_BITFIELDS, BI_ALPHABITFIELDS
        raise ClipboardImageError("剪贴板位图使用了不支持的压缩格式。")
    if bit_count == 24 and compression != 0:
        raise ClipboardImageError("24 位剪贴板位图只支持 BI_RGB 格式。")

    offset = header_size
    masks = None
    if bit_count in {16, 32}:
        if header_size >= 52:
            masks = struct.unpack_from("<III", dib, 40)
        elif compression in {3, 6}:
            count = 4 if compression == 6 else 3
            end = header_size + count * 4
            if end > len(dib):
                raise ClipboardImageError("剪贴板位图缺少颜色掩码。")
            masks = struct.unpack_from("<" + "I" * count, dib, header_size)[:3]
            offset = end
        elif bit_count == 16:
            masks = (0x7c00, 0x03e0, 0x001f)
        else:
            masks = (0x00ff0000, 0x0000ff00, 0x000000ff)

    row_stride = ((width * bit_count + 31) // 32) * 4
    required = offset + row_stride * height
    if required > len(dib):
        raise ClipboardImageError("剪贴板位图像素数据不完整。")
    if required > MAX_CLIPBOARD_BYTES:
        raise ClipboardImageError("剪贴板位图数据超过安全处理上限。")

    compressor = zlib.compressobj()
    compressed = []
    bytes_per_pixel = bit_count // 8
    for out_y in range(height):
        source_y = height - 1 - out_y if signed_height > 0 else out_y
        start = offset + source_y * row_stride
        row = dib[start:start + row_stride]
        rgb = bytearray(width * 3)
        for x in range(width):
            pos = x * bytes_per_pixel
            if bit_count == 24:
                blue, green, red = row[pos:pos + 3]
            else:
                pixel = int.from_bytes(row[pos:pos + bytes_per_pixel], "little")
                red = _channel(pixel, masks[0])
                green = _channel(pixel, masks[1])
                blue = _channel(pixel, masks[2])
            target = x * 3
            rgb[target:target + 3] = bytes((red, green, blue))
        compressed.append(compressor.compress(b"\x00" + rgb))
    compressed.append(compressor.flush())
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (PNG_SIGNATURE + _png_chunk(b"IHDR", ihdr) +
            _png_chunk(b"IDAT", b"".join(compressed)) + _png_chunk(b"IEND", b""))


def _read_global(user32, kernel32, fmt: int):
    handle = user32.GetClipboardData(fmt)
    if not handle:
        return None
    size = kernel32.GlobalSize(handle)
    if not size:
        return None
    if size > MAX_CLIPBOARD_BYTES:
        raise ClipboardImageError("剪贴板图片超过 64 MB。")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        return None
    try:
        return ctypes.string_at(pointer, size)
    finally:
        kernel32.GlobalUnlock(handle)


def capture_clipboard_image(destination_dir: str | None = None) -> str | None:
    """Save a clipboard PNG/DIB as a PNG file; return None when clipboard has no image."""
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClipboardFormatW.restype = wintypes.UINT
        kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalSize.restype = ctypes.c_size_t
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
    except (AttributeError, OSError) as exc:
        raise ClipboardImageError("无法访问 Windows 剪贴板：%s" % exc) from exc

    if not user32.OpenClipboard(None):
        return None
    raw_png = None
    raw_dib = None
    try:
        png_format = user32.RegisterClipboardFormatW("PNG")
        if png_format and user32.IsClipboardFormatAvailable(png_format):
            raw_png = _read_global(user32, kernel32, png_format)
        if raw_png is None or not raw_png.startswith(PNG_SIGNATURE):
            for fmt in (CF_DIBV5, CF_DIB):
                if user32.IsClipboardFormatAvailable(fmt):
                    raw_dib = _read_global(user32, kernel32, fmt)
                    if raw_dib is not None:
                        break
    finally:
        user32.CloseClipboard()

    if raw_png is not None and raw_png.startswith(PNG_SIGNATURE):
        png = raw_png
    elif raw_dib is not None:
        png = dib_to_png(raw_dib)
    elif raw_png is not None:
        raise ClipboardImageError("剪贴板 PNG 数据格式无效。")
    else:
        return None

    folder = (Path(destination_dir) if destination_dir else
              Path(tempfile.gettempdir()) / "ai_modeler" / "clipboard")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("clipboard-%s.png" % uuid.uuid4().hex)
    path.write_bytes(png)
    return str(path)

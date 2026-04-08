"""B3DM 后处理模块 — 纯 Python 实现，无需 Node.js
WebP: Pillow | KTX2: Pillow + toktx | Draco: DracoPy
"""
import io
import json
import os
import struct
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    import DracoPy
    HAS_DRACO = True
except ImportError:
    HAS_DRACO = False

_APP_DIR = str(Path(__file__).resolve().parent)

# toktx 路径探测
for _p in [
    os.path.join(_APP_DIR, "ktx", "bin"),
    r"C:\Program Files\KTX-Software\bin",
    "/usr/local/bin", "/usr/bin",
]:
    if os.path.isfile(os.path.join(_p, "toktx" + (".exe" if os.name == "nt" else ""))):
        if _p not in os.environ.get("PATH", ""):
            os.environ["PATH"] = _p + os.pathsep + os.environ.get("PATH", "")
        break


# ===================== GLB 二进制操作 =====================

def parse_glb(data):
    """解析 GLB -> (gltf_dict, bin_bytes)"""
    if len(data) < 12:
        return None, b""
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        return None, b""
    offset, gltf, bin_data = 12, None, b""
    while offset + 8 <= len(data):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8: offset + 8 + chunk_len]
        if chunk_type == 0x4E4F534A:
            gltf = json.loads(chunk.rstrip(b"\x00 ").decode("utf-8"))
        elif chunk_type == 0x004E4942:
            bin_data = bytes(chunk)
        offset += 8 + chunk_len
    return gltf, bin_data


def build_glb(gltf, bin_data):
    """从 gltf dict + bin 数据重建GLB"""
    js = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    bp = (4 - len(bin_data) % 4) % 4 if bin_data else 0
    bd = bin_data + b"\x00" * bp
    total = 12 + 8 + len(js) + (8 + len(bd) if bd else 0)
    buf = bytearray(struct.pack("<III", 0x46546C67, 2, total))
    buf += struct.pack("<II", len(js), 0x4E4F534A) + js
    if bd:
        buf += struct.pack("<II", len(bd), 0x004E4942) + bd
    return bytes(buf)


def replace_textures_in_glb(glb_bytes, converter_fn, extension=None):
    """替换 GLB 内所有纹理，返回新 GLB"""
    gltf, bin_data = parse_glb(glb_bytes)
    if not gltf or not bin_data:
        return glb_bytes
    images = gltf.get("images", [])
    bvs = gltf.get("bufferViews", [])
    if not images:
        return glb_bytes

    img_bv = {}
    for i, img in enumerate(images):
        bv = img.get("bufferView")
        if bv is not None:
            img_bv[bv] = i

    bv_data = []
    for bv in bvs:
        off = bv.get("byteOffset", 0)
        bv_data.append(bytearray(bin_data[off: off + bv["byteLength"]]))

    changed = False
    for bv_idx, img_idx in img_bv.items():
        mime = images[img_idx].get("mimeType", "image/png")
        new_bytes, new_mime = converter_fn(bytes(bv_data[bv_idx]), mime)
        if new_bytes:
            bv_data[bv_idx] = bytearray(new_bytes)
            images[img_idx]["mimeType"] = new_mime
            changed = True

    if not changed:
        return glb_bytes

    new_bin = _rebuild_bin_buffer(gltf, bvs, bv_data)

    if extension:
        for key in ("extensionsUsed", "extensionsRequired"):
            lst = gltf.setdefault(key, [])
            if extension not in lst:
                lst.append(extension)
    return build_glb(gltf, bytes(new_bin))


# ===================== GLB Accessor 读取 =====================

def _rebuild_bin_buffer(gltf, bvs, bv_data_list):
    """重建 GLB BIN 缓冲区（4 字节对齐），更新 bufferView offset 和 buffer size"""
    new_bin = bytearray()
    for idx, data in enumerate(bv_data_list):
        new_bin += b'\x00' * ((4 - len(new_bin) % 4) % 4)
        bvs[idx]['byteOffset'] = len(new_bin)
        bvs[idx]['byteLength'] = len(data)
        new_bin += data
    if gltf.get('buffers'):
        gltf['buffers'][0]['byteLength'] = len(new_bin)
    return bytes(new_bin)

_COMP_TYPE = {
    5120: ('b', 1, np.int8), 5121: ('B', 1, np.uint8),
    5122: ('h', 2, np.int16), 5123: ('H', 2, np.uint16),
    5125: ('I', 4, np.uint32), 5126: ('f', 4, np.float32),
}
_TYPE_N = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}


def _read_accessor(gltf, bin_data, acc_idx):
    """读取 accessor 数据为 numpy 数组 (count, n_components)"""
    acc = gltf['accessors'][acc_idx]
    bv = gltf['bufferViews'][acc['bufferView']]
    fmt, csz, dtype = _COMP_TYPE[acc['componentType']]
    nc = _TYPE_N[acc['type']]
    count = acc['count']
    bv_off = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
    stride = bv.get('byteStride', csz * nc)
    element_size = csz * nc

    if stride == element_size:
        # 紧凑排列 — 直接 frombuffer，性能最优
        raw = np.frombuffer(bin_data, dtype=f'<{fmt}', count=count * nc, offset=bv_off)
        out = raw.reshape(count, nc)
    else:
        # 有 stride — 按行切片
        out = np.empty((count, nc), dtype=dtype)
        for i in range(count):
            base = bv_off + i * stride
            out[i] = np.frombuffer(bin_data, dtype=f'<{fmt}', count=nc, offset=base)

    if fmt == 'f':
        return out.astype(np.float32, copy=False)
    return out.astype(np.uint32, copy=False)


def _draco_compress_glb(glb_bytes):
    """纯 Python Draco 压缩 GLB 中所有网格"""
    gltf, bin_data = parse_glb(glb_bytes)
    if not gltf or not bin_data:
        return glb_bytes

    meshes = gltf.get('meshes', [])
    if not meshes:
        return glb_bytes

    bvs = gltf['bufferViews']
    # 收集所有 bufferView 的原始数据
    bv_data_list = []
    for bv in bvs:
        off = bv.get('byteOffset', 0)
        bv_data_list.append(bin_data[off: off + bv['byteLength']])

    # 对每个 primitive 做 Draco 压缩
    for mesh in meshes:
        for prim in mesh.get('primitives', []):
            attrs = prim.get('attributes', {})
            if 'POSITION' not in attrs:
                continue

            pos = _read_accessor(gltf, bin_data, attrs['POSITION'])
            points = np.ascontiguousarray(pos, dtype=np.float32)

            faces = None
            if 'indices' in prim:
                idx = _read_accessor(gltf, bin_data, prim['indices'])
                faces = np.ascontiguousarray(idx.reshape(-1, 3).astype(np.uint32))

            normals = None
            if 'NORMAL' in attrs:
                normals = np.ascontiguousarray(
                    _read_accessor(gltf, bin_data, attrs['NORMAL']), dtype=np.float32)

            tex_coord = None
            if 'TEXCOORD_0' in attrs:
                tex_coord = np.ascontiguousarray(
                    _read_accessor(gltf, bin_data, attrs['TEXCOORD_0']), dtype=np.float32)

            encoded = DracoPy.encode(
                points, faces=faces, normals=normals, tex_coord=tex_coord,
                quantization_bits=11, compression_level=7,
                preserve_order=False
            )
            if not encoded:
                continue

            # 新建 bufferView 存放 Draco 数据
            draco_bv_idx = len(bvs)
            bvs.append({'buffer': 0, 'byteOffset': 0, 'byteLength': len(encoded)})
            bv_data_list.append(encoded)

            # 构建 extension 的 attributes 映射
            draco_attrs = {'POSITION': 0}
            attr_id = 1
            if normals is not None:
                draco_attrs['NORMAL'] = attr_id; attr_id += 1
            if tex_coord is not None:
                draco_attrs['TEXCOORD_0'] = attr_id; attr_id += 1

            prim.setdefault('extensions', {})['KHR_draco_mesh_compression'] = {
                'bufferView': draco_bv_idx,
                'attributes': draco_attrs,
            }

    # 注册扩展
    for key in ('extensionsUsed', 'extensionsRequired'):
        lst = gltf.setdefault(key, [])
        if 'KHR_draco_mesh_compression' not in lst:
            lst.append('KHR_draco_mesh_compression')

    new_bin = _rebuild_bin_buffer(gltf, bvs, bv_data_list)

    return build_glb(gltf, new_bin)


# ===================== 图片转换器 =====================

def _to_webp(image_bytes, _mime):
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in getattr(img, "mode", "") else "RGB")
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=85)
    return out.getvalue(), "image/webp"


def _make_ktx2_converter(mode="uastc"):
    def _convert(image_bytes, _mime):
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        nw, nh = (w + 3) // 4 * 4, (h + 3) // 4 * 4
        if (nw, nh) != (w, h):
            img = img.resize((nw, nh), Image.LANCZOS)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img.save(f, format="PNG")
            pin = f.name
        pout = pin + ".ktx2"
        try:
            cmd = ["toktx", "--t2"]
            if mode == "uastc":
                cmd += ["--uastc", "2", "--zcmp", "18"]
            else:
                cmd += ["--bcmp"]
            cmd += [pout, pin]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=120)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout or "").strip()
                                   or f"toktx code {r.returncode}")
            with open(pout, "rb") as f:
                return f.read(), "image/ktx2"
        finally:
            Path(pin).unlink(missing_ok=True)
            Path(pout).unlink(missing_ok=True)
    return _convert


# ===================== B3DM 操作 =====================

def extract_glb_from_b3dm(b3dm_path):
    with open(b3dm_path, "rb") as f:
        header = f.read(28)
    magic, ver, blen, ftj, ftb, btj, btb = struct.unpack("<4sIIIIII", header)
    if magic != b"b3dm":
        raise ValueError(f"Not a b3dm: {b3dm_path}")
    off = 28 + ftj + ftb + btj + btb
    with open(b3dm_path, "rb") as f:
        pre = f.read(off)
        glb = f.read()
    return pre, glb


def repack_b3dm(path, pre, new_glb):
    total = len(pre) + len(new_glb)
    hdr = bytearray(pre[:28])
    struct.pack_into("<I", hdr, 8, total)
    with open(path, "wb") as f:
        f.write(bytes(hdr) + pre[28:] + new_glb)


def _process_b3dm_python(b3dm_path, ops):
    """纯 Python 管线处理单个 B3DM"""
    pre, glb = extract_glb_from_b3dm(b3dm_path)
    if not glb or len(glb) < 12:
        return False
    for op in ops:
        glb = op(glb)
    repack_b3dm(b3dm_path, pre, glb)
    return True


# ===================== 主入口 =====================

def run_postprocess(output_dir, options, log_fn=None):
    if not log_fn:
        log_fn = lambda msg: None

    py_ops = []
    labels = []

    if options.get("draco"):
        if HAS_DRACO:
            py_ops.insert(0, _draco_compress_glb)
            labels.append("Draco")
        else:
            log_fn("⚠ Draco 已跳过: 需要 pip install DracoPy numpy")

    if options.get("webp"):
        if HAS_PILLOW:
            py_ops.append(lambda glb: replace_textures_in_glb(glb, _to_webp))
            labels.append("WebP")
        else:
            log_fn("⚠ WebP 已跳过: 需要 pip install Pillow")

    if options.get("ktx2"):
        toktx_ok = subprocess.run("toktx --version",
                                  capture_output=True, shell=True).returncode == 0
        if toktx_ok and HAS_PILLOW:
            mode = options.get("ktx2_mode", "uastc")
            conv = _make_ktx2_converter(mode)
            py_ops.append(lambda glb, c=conv: replace_textures_in_glb(
                glb, c, "KHR_texture_basisu"))
            labels.append(f"KTX2({mode})")
        else:
            missing = []
            if not HAS_PILLOW: missing.append("Pillow")
            if not toktx_ok: missing.append("toktx")
            log_fn(f"⚠ KTX2 已跳过: 需要 {', '.join(missing)}")

    if not py_ops:
        return

    b3dm_files = list(Path(output_dir).rglob("*.b3dm"))
    if not b3dm_files:
        log_fn("未找到 B3DM 文件，跳过后处理")
        return

    total = len(b3dm_files)
    log_fn(f"开始后处理: {total} 个 B3DM 文件")
    log_fn(f"启用: {', '.join(labels)}")

    success = 0
    workers = min(os.cpu_count() or 4, total, 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, b3dm in enumerate(b3dm_files):
            fut = pool.submit(_process_b3dm_python, str(b3dm), py_ops)
            futures[fut] = (i, b3dm)
        for fut in as_completed(futures):
            i, b3dm = futures[fut]
            log_fn(f"[{i+1}/{total}] {b3dm.name}")
            try:
                if fut.result():
                    success += 1
            except Exception as e:
                log_fn(f"  处理失败: {e}")

    log_fn(f"后处理完成: {success}/{total} 个文件成功")

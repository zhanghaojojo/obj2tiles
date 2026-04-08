"""管线数据批量转换模块

处理按 GUID 组织的管线 OBJ 数据，每个 GUID 包含：
- {GUID}.obj  - 3D 模型
- {GUID}.mtl  - 材质定义
- {GUID}.json - 元数据（含坐标系和原点信息）

JSON 格式示例：
{
    "DataType": "管线点",
    "name": "",
    "src": "EPSG:4550",
    "srsorigin": "4626754.366641063243,536019.3180787251331,0"
}
"""

import os
import re
import json
import shutil
import math
from pathlib import Path

GUID_PATTERN = re.compile(
    r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$"
)
TEXTURE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def _read_text(filepath):
    """多编码尝试读取文本文件，返回行列表"""
    for enc in ("utf-8-sig", "gbk", "gb2312", "latin-1"):
        try:
            with open(filepath, "r", encoding=enc) as f:
                return f.readlines()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def scan_pipeline_directory(dir_path):
    """扫描管线目录，识别所有 GUID 模型及其元数据"""
    dir_path = Path(dir_path)
    if not dir_path.is_dir():
        return {"error": f"目录不存在: {dir_path}"}

    items = []
    epsg_set = set()

    for f in sorted(dir_path.iterdir()):
        if f.suffix.lower() != ".json":
            continue
        json_file = f
        if json_file.name.lower() == "config.json":
            continue
        stem = json_file.stem
        if not GUID_PATTERN.match(stem):
            continue
        obj_file = dir_path / (stem + ".obj")
        if not obj_file.exists():
            continue
        try:
            with open(json_file, "r", encoding="utf-8-sig") as jf:
                meta = json.load(jf)
        except Exception:
            continue

        src = meta.get("src", "")
        srsorigin = meta.get("srsorigin", "")
        if src:
            epsg_set.add(src)

        items.append(
            {
                "guid": stem,
                "obj_file": str(obj_file),
                "mtl_file": str(dir_path / (stem + ".mtl")),
                "data_type": meta.get("DataType", ""),
                "name": meta.get("name", ""),
                "src": src,
                "srsorigin": srsorigin,
            }
        )

    textures = [f.name for f in dir_path.iterdir() if f.suffix.lower() in TEXTURE_EXTS]

    return {
        "count": len(items),
        "items": items,
        "epsg_list": list(epsg_set),
        "textures": textures,
        "directory": str(dir_path),
    }


def parse_srsorigin(srsorigin_str):
    """解析 srsorigin 字符串为 (comp0, comp1, comp2)"""
    parts = [p.strip() for p in srsorigin_str.split(",")]
    comp0 = float(parts[0]) if len(parts) > 0 and parts[0] else 0.0
    comp1 = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
    comp2 = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
    return comp0, comp1, comp2


def compute_center_origin(items):
    """计算所有模型 srsorigin 的几何中心"""
    if not items:
        return (0.0, 0.0, 0.0)
    sum0, sum1, sum2 = 0.0, 0.0, 0.0
    count = 0
    for item in items:
        if not item.get("srsorigin"):
            continue
        o = parse_srsorigin(item["srsorigin"])
        sum0 += o[0]
        sum1 += o[1]
        sum2 += o[2]
        count += 1
    if count == 0:
        return (0.0, 0.0, 0.0)
    return (sum0 / count, sum1 / count, sum2 / count)


_transformer_cache = {}


def convert_to_wgs84(epsg_code, srsorigin_tuple):
    """将投影坐标转为 WGS84

    srsorigin_tuple: (comp0, comp1, comp2) 从 srsorigin 解析
    EPSG:4550 等投影坐标系: comp0=northing, comp1=easting, comp2=elevation

    返回: (lat, lon, alt)
    """
    from pyproj import Transformer

    src_code = epsg_code.strip()
    if not src_code.upper().startswith("EPSG:"):
        src_code = "EPSG:" + src_code

    comp0, comp1, comp2 = srsorigin_tuple
    northing = comp0
    easting = comp1
    elevation = comp2

    cache_key = (src_code.upper(), "EPSG:4326")
    if cache_key not in _transformer_cache:
        _transformer_cache[cache_key] = Transformer.from_crs(src_code, "EPSG:4326", always_xy=True)
    transformer = _transformer_cache[cache_key]
    lon, lat = transformer.transform(easting, northing)

    return lat, lon, elevation


def wgs84_to_ecef(lat_deg, lon_deg, alt):
    """WGS84 经纬度转 ECEF 笛卡尔坐标"""
    a = 6378137.0
    f = 1.0 / 298.257223563
    e2 = 2 * f - f * f

    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)

    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)

    N = a / math.sqrt(1 - e2 * sin_lat * sin_lat)

    X = (N + alt) * cos_lat * cos_lon
    Y = (N + alt) * cos_lat * sin_lon
    Z = (N * (1 - e2) + alt) * sin_lat

    return X, Y, Z


def enu_to_ecef_transform(lat_deg, lon_deg, alt):
    """计算 ENU→ECEF 的 4x4 变换矩阵（列优先 flat array，3D Tiles 格式）"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)

    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)

    X, Y, Z = wgs84_to_ecef(lat_deg, lon_deg, alt)

    # 列优先 4x4: [col0, col1, col2, col3]
    # col0 = East, col1 = North, col2 = Up, col3 = Translation
    return [
        -sin_lon,                -sin_lat * cos_lon,     cos_lat * cos_lon,  0,
        cos_lon,                 -sin_lat * sin_lon,     cos_lat * sin_lon,  0,
        0,                       cos_lat,                sin_lat,            0,
        X,                       Y,                      Z,                  1,
    ]


def merge_pipeline_objs(items, center_origin, work_dir, log_fn=None):
    """合并多个 GUID OBJ 为一个文件

    items: scan 结果中的 items 列表
    center_origin: (comp0, comp1, comp2) 中心点原点
    work_dir: 工作目录（输出合并文件）
    log_fn: 日志回调

    返回: 合并后的 OBJ 文件路径
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    merged_obj_path = work_dir / "merged.obj"
    merged_mtl_path = work_dir / "merged.mtl"

    total = len(items)
    vertex_offset = 0
    vt_offset = 0
    vn_offset = 0
    material_index = 0

    all_mtl_lines = []
    texture_files = set()
    source_dir = None

    with open(merged_obj_path, "w", encoding="utf-8") as obj_out:
        obj_out.write("# Merged pipeline OBJ\n")
        obj_out.write("mtllib merged.mtl\n\n")

        for idx, item in enumerate(items):
            if log_fn and idx % 50 == 0:
                log_fn(f"合并模型 {idx + 1}/{total} ...")

            if not item.get("srsorigin"):
                continue

            origin = parse_srsorigin(item["srsorigin"])
            delta = (
                origin[0] - center_origin[0],
                origin[1] - center_origin[1],
                origin[2] - center_origin[2],
            )

            obj_path = Path(item["obj_file"])
            mtl_path = Path(item["mtl_file"])

            if source_dir is None:
                source_dir = obj_path.parent

            # 读取 MTL，重映射材质名
            mat_remap = {}
            if mtl_path.exists():
                for line in _read_text(str(mtl_path)):
                    stripped = line.strip()
                    if stripped.startswith("newmtl "):
                        old_name = stripped[7:].strip()
                        new_name = f"m{material_index}_{old_name}"
                        mat_remap[old_name] = new_name
                        all_mtl_lines.append(f"newmtl {new_name}\n")
                        material_index += 1
                    elif any(
                        stripped.startswith(p)
                        for p in ("map_Kd ", "map_Ka ", "map_Ks ")
                    ):
                        parts = stripped.split(None, 1)
                        if len(parts) > 1:
                            tex_path = Path(parts[1])
                            # 防止纹理路径逃逸（路径穿越）
                            if not tex_path.is_absolute() and ".." not in tex_path.parts:
                                texture_files.add(parts[1])
                        all_mtl_lines.append(line)
                    else:
                        all_mtl_lines.append(line)

            # 读取并处理 OBJ
            v_count = 0
            vt_count = 0
            vn_count = 0

            obj_out.write(f"g {item['guid']}\n")

            for line in _read_text(str(obj_path)):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                if stripped.startswith("v "):
                    parts = stripped.split()
                    if len(parts) >= 4:
                        x = float(parts[1]) + delta[0]
                        y = float(parts[2]) + delta[1]
                        z = float(parts[3]) + delta[2]
                        obj_out.write(f"v {x} {y} {z}\n")
                        v_count += 1
                elif stripped.startswith("vt "):
                    obj_out.write(line)
                    vt_count += 1
                elif stripped.startswith("vn "):
                    obj_out.write(line)
                    vn_count += 1
                elif stripped.startswith("f "):
                    parts = stripped.split()
                    new_parts = ["f"]
                    for p in parts[1:]:
                        indices = p.split("/")
                        new_indices = []
                        for i, idx_str in enumerate(indices):
                            if idx_str:
                                val = int(idx_str)
                                if i == 0:
                                    val += vertex_offset
                                elif i == 1:
                                    val += vt_offset
                                elif i == 2:
                                    val += vn_offset
                                new_indices.append(str(val))
                            else:
                                new_indices.append("")
                        new_parts.append("/".join(new_indices))
                    obj_out.write(" ".join(new_parts) + "\n")
                elif stripped.startswith("usemtl "):
                    mat_name = stripped[7:].strip()
                    new_name = mat_remap.get(mat_name, mat_name)
                    obj_out.write(f"usemtl {new_name}\n")
                elif stripped.startswith("mtllib "):
                    pass  # 跳过原始 mtllib 引用

            vertex_offset += v_count
            vt_offset += vt_count
            vn_offset += vn_count

    # 写入合并的 MTL
    with open(merged_mtl_path, "w", encoding="utf-8") as f:
        f.writelines(all_mtl_lines)

    # 复制纹理文件
    if source_dir:
        for tex in texture_files:
            src = source_dir / tex
            dst = work_dir / tex
            if src.exists() and not dst.exists():
                shutil.copy2(str(src), str(dst))

    if log_fn:
        log_fn(
            f"合并完成: {total} 个模型, {vertex_offset} 个顶点, "
            f"{material_index} 个材质, {len(texture_files)} 个纹理"
        )

    return str(merged_obj_path)

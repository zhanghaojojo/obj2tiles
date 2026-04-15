import os
import sys
import uuid
import json
import math
import subprocess
import platform
import zipfile
import shutil
import datetime
import threading
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template, send_file
from flask_cors import CORS
from postprocess import run_postprocess
from werkzeug.utils import secure_filename
from pipeline_convert import (
    scan_pipeline_directory,
    parse_srsorigin,
    compute_center_origin,
    convert_to_wgs84,
    merge_pipeline_objs,
    enu_to_ecef_transform,
)

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
TOOLS_DIR = BASE_DIR / "tools"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
HISTORY_FILE = BASE_DIR / "history.json"
_history_lock = threading.Lock()
_task_pool = ThreadPoolExecutor(max_workers=4)


def load_history():
    with _history_lock:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return []


def save_history(records):
    with _history_lock:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)


def append_history(record):
    with _history_lock:
        records = []
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                records = json.load(f)
        records.insert(0, record)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)


TOOLS_DIR.mkdir(exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB

# 任务状态存储
tasks = {}


def mat4_multiply(a, b):
    """4x4 矩阵乘法（列优先数组）"""
    result = [0.0] * 16
    for row in range(4):
        for col in range(4):
            s = 0.0
            for k in range(4):
                s += a[row + k * 4] * b[k + col * 4]
            result[row + col * 4] = s
    return result


def rotation_matrix(rx_deg, ry_deg, rz_deg):
    """根据 X/Y/Z 轴旋转角度（度）生成 4x4 旋转矩阵（列优先）"""
    rx = math.radians(rx_deg)
    ry = math.radians(ry_deg)
    rz = math.radians(rz_deg)

    # 绕 X
    cx, sx = math.cos(rx), math.sin(rx)
    mx = [
        1, 0, 0, 0,
        0, cx, sx, 0,
        0, -sx, cx, 0,
        0, 0, 0, 1,
    ]
    # 绕 Y
    cy, sy = math.cos(ry), math.sin(ry)
    my = [
        cy, 0, -sy, 0,
        0, 1, 0, 0,
        sy, 0, cy, 0,
        0, 0, 0, 1,
    ]
    # 绕 Z
    cz, sz = math.cos(rz), math.sin(rz)
    mz = [
        cz, sz, 0, 0,
        -sz, cz, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ]
    return mat4_multiply(mz, mat4_multiply(my, mx))


def apply_rotation_to_tileset(tileset_path, rx, ry, rz):
    """对 tileset.json 的 root.transform 施加旋转修正"""
    if rx == 0 and ry == 0 and rz == 0:
        return
    with open(tileset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    identity = [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ]
    existing = data.get("root", {}).get("transform", identity)
    rot = rotation_matrix(rx, ry, rz)
    new_transform = mat4_multiply(existing, rot)

    if "root" not in data:
        data["root"] = {}
    data["root"]["transform"] = [round(v, 10) for v in new_transform]

    with open(tileset_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_obj2tiles_path():
    """获取 Obj2Tiles 可执行文件路径"""
    system = platform.system().lower()
    if system == "windows":
        exe = TOOLS_DIR / "Obj2Tiles.exe"
    else:
        exe = TOOLS_DIR / "Obj2Tiles"
    if exe.exists():
        return str(exe)
    return None


def run_conversion(task_id, input_obj, output_dir, options):
    """在后台线程中执行转换"""
    try:
        tasks[task_id]["status"] = "running"
        tasks[task_id]["message"] = "正在转换中..."

        exe = get_obj2tiles_path()
        if not exe:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["message"] = "Obj2Tiles 未找到，请将可执行文件放入 tools/ 目录"
            return

        cmd = [exe, str(input_obj), str(output_dir)]

        lat = options.get("lat")
        lon = options.get("lon")
        alt = options.get("alt")
        if lat is not None and lon is not None:
            cmd.extend(["--lat", str(lat), "--lon", str(lon)])
        if alt is not None:
            cmd.extend(["--alt", str(alt)])
        if options.get("y_up_to_z_up"):
            cmd.append("--y-up-to-z-up")
        if options.get("local"):
            cmd.append("--local")
        if options.get("keep_textures"):
            cmd.append("--keeptextures")
        if options.get("lods") is not None:
            cmd.extend(["--lods", str(options["lods"])])
        if options.get("divisions") is not None:
            cmd.extend(["--divisions", str(options["divisions"])])
        if options.get("error") is not None:
            cmd.extend(["--error", str(options["error"])])
        if options.get("scale") is not None:
            cmd.extend(["--scale", str(options["scale"])])
        if options.get("split_strategy"):
            cmd.extend(["--split-strategy", options["split_strategy"]])
        if options.get("zsplit"):
            cmd.append("--zsplit")
        if options.get("use_system_temp"):
            cmd.append("--use-system-temp")
        if options.get("keep_intermediate"):
            cmd.append("--keep-intermediate")
        if options.get("stage"):
            cmd.extend(["--stage", options["stage"]])

        tasks[task_id]["message"] = f"执行命令: {' '.join(cmd)}"

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(Path(input_obj).parent),
        )

        output_lines = []
        for line in process.stdout:
            line = line.strip()
            if line:
                output_lines.append(line)
                tasks[task_id]["log"] = "\n".join(output_lines[-50:])
                tasks[task_id]["message"] = line

        process.wait()

        if process.returncode == 0:
            tileset_path = Path(output_dir) / "tileset.json"
            if tileset_path.exists():
                rx = options.get("rotate_x", 0)
                ry = options.get("rotate_y", 0)
                rz = options.get("rotate_z", 0)
                if rx or ry or rz:
                    tasks[task_id]["message"] = "正在应用旋转修正..."
                    apply_rotation_to_tileset(str(tileset_path), rx, ry, rz)
                pp_opts = {
                    "draco": options.get("pp_draco"),
                    "webp": options.get("pp_webp"),
                    "ktx2": options.get("pp_ktx2"),
                    "ktx2_mode": options.get("pp_ktx2_mode", "uastc"),
                }
                if any(pp_opts.values()):
                    tasks[task_id]["message"] = "正在后处理..."
                    def pp_log(msg):
                        tasks[task_id]["log"] += "\n" + msg
                        tasks[task_id]["message"] = msg
                    run_postprocess(str(output_dir), pp_opts, pp_log)
                tasks[task_id]["status"] = "done"
                tasks[task_id]["message"] = "转换完成"
                tasks[task_id]["tileset_url"] = f"/output/{task_id}/tileset.json"
                append_history({
                    "task_id": task_id,
                    "obj_name": Path(input_obj).name,
                    "mode": tasks[task_id].get("mode", "octree"),
                    "status": "done",
                    "tileset_url": tasks[task_id]["tileset_url"],
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
            else:
                tasks[task_id]["status"] = "error"
                tasks[task_id]["message"] = "转换完成但未生成 tileset.json"
        else:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["message"] = f"转换失败 (返回码: {process.returncode})"

    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["message"] = f"转换异常: {str(e)}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    """上传 OBJ 文件（支持 zip 包或多文件上传）"""
    files = request.files.getlist("files")
    if not files or all(not f.filename for f in files):
        return jsonify({"error": "未选择文件"}), 400

    task_id = str(uuid.uuid4())[:8]
    task_upload_dir = UPLOAD_DIR / task_id
    task_output_dir = OUTPUT_DIR / task_id
    task_upload_dir.mkdir(parents=True, exist_ok=True)
    task_output_dir.mkdir(parents=True, exist_ok=True)

    obj_file = None

    for f in files:
        if not f.filename:
            continue
        safe_name = secure_filename(f.filename)
        if not safe_name:
            continue
        save_path = task_upload_dir / safe_name
        f.save(str(save_path))

        if safe_name.lower().endswith(".zip"):
            with zipfile.ZipFile(str(save_path), "r") as zf:
                # Zip Slip 防护：校验所有条目路径不逃逸目标目录
                for member in zf.namelist():
                    member_path = (task_upload_dir / member).resolve()
                    if not str(member_path).startswith(str(task_upload_dir.resolve())):
                        return jsonify({"error": f"ZIP 包含非法路径: {member}"}), 400
                zf.extractall(str(task_upload_dir))
            os.remove(str(save_path))

    obj_files = list(task_upload_dir.rglob("*.obj"))

    if not obj_files:
        return jsonify({"error": "未在上传内容中找到 .obj 文件"}), 400

    options = {
        "lat": request.form.get("lat", type=float),
        "lon": request.form.get("lon", type=float),
        "alt": request.form.get("alt", type=float, default=0),
        "y_up_to_z_up": request.form.get("y_up_to_z_up") == "true",
        "local": request.form.get("local") == "true",
        "keep_textures": request.form.get("keep_textures") == "true",
        "lods": request.form.get("lods", type=int),
        "divisions": request.form.get("divisions", type=int),
        "error": request.form.get("error", type=float),
        "scale": request.form.get("scale", type=float),
        "split_strategy": request.form.get("split_strategy"),
        "rotate_x": request.form.get("rotate_x", type=float, default=0),
        "rotate_y": request.form.get("rotate_y", type=float, default=0),
        "rotate_z": request.form.get("rotate_z", type=float, default=0),
        "zsplit": request.form.get("zsplit") == "true",
        "use_system_temp": request.form.get("use_system_temp") == "true",
        "keep_intermediate": request.form.get("keep_intermediate") == "true",
        "stage": request.form.get("stage", default=""),
        "pp_draco": request.form.get("pp_draco") == "true",
        "pp_webp": request.form.get("pp_webp") == "true",
        "pp_ktx2": request.form.get("pp_ktx2") == "true",
        "pp_ktx2_mode": request.form.get("pp_ktx2_mode", default="uastc"),
    }
    mode = request.form.get("mode", "octree")

    created_ids = []
    for obj_file in obj_files:
        tid = task_id if len(obj_files) == 1 else str(uuid.uuid4())[:8]
        tid_output = OUTPUT_DIR / tid
        tid_output.mkdir(parents=True, exist_ok=True)
        tasks[tid] = {
            "status": "queued",
            "message": "排队中...",
            "log": "",
            "tileset_url": None,
            "obj_file": str(obj_file),
            "obj_name": obj_file.name,
            "output_dir": str(tid_output),
            "mode": mode,
        }
        _task_pool.submit(run_conversion, tid, obj_file, tid_output, options)
        created_ids.append(tid)

    return jsonify({"task_ids": created_ids, "task_id": created_ids[0], "message": f"上传成功，创建 {len(created_ids)} 个任务"})


@app.route("/api/status/<task_id>")
def task_status(task_id):
    """查询转换任务状态"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({
        "task_id": task_id,
        "status": task["status"],
        "message": task["message"],
        "log": task.get("log", ""),
        "tileset_url": task.get("tileset_url"),
    })


@app.route("/output/<path:filepath>")
def serve_output(filepath):
    """提供转换后的 3D Tiles 文件静态服务"""
    return send_from_directory(str(OUTPUT_DIR), filepath)


@app.route("/api/download/<task_id>")
def download_output(task_id):
    """将任务输出目录打包为 ZIP 下载"""
    # 验证 task_id 格式，防止路径穿越
    safe_id = secure_filename(task_id)
    if not safe_id or safe_id != task_id:
        return jsonify({"error": "无效的任务 ID"}), 400

    output_path = OUTPUT_DIR / safe_id
    if not output_path.is_dir():
        return jsonify({"error": "任务输出目录不存在"}), 404

    # 确认目录内有文件
    files = list(output_path.rglob("*"))
    files = [f for f in files if f.is_file()]
    if not files:
        return jsonify({"error": "输出目录为空，无可下载内容"}), 404

    zip_fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(zip_fd)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                arcname = f.relative_to(output_path)
                zf.write(str(f), str(arcname))
        return send_file(
            zip_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"3dtiles_{safe_id}.zip",
        )
    except Exception as e:
        return jsonify({"error": f"打包失败: {str(e)}"}), 500
    finally:
        # send_file with a path will read the file, so schedule cleanup
        # Flask's send_file handles the file before this, but to be safe
        # we use a background thread for cleanup
        def _cleanup():
            import time
            time.sleep(60)
            try:
                os.unlink(zip_path)
            except OSError:
                pass
        threading.Thread(target=_cleanup, daemon=True).start()


@app.route("/api/check-tool")
def check_tool():
    """检查 Obj2Tiles 工具是否就绪"""
    exe = get_obj2tiles_path()
    return jsonify({
        "ready": exe is not None,
        "path": exe,
        "platform": platform.system(),
    })


@app.route("/api/tasks")
def list_tasks():
    """列出所有任务"""
    result = []
    for tid, t in tasks.items():
        result.append({
            "task_id": tid,
            "status": t["status"],
            "message": t["message"],
            "tileset_url": t.get("tileset_url"),
            "mode": t.get("mode", "octree"),
            "obj_name": t.get("obj_name", ""),
        })
    return jsonify(result)


@app.route("/api/delete/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    """删除任务及其文件"""
    task = tasks.pop(task_id, None)
    upload_path = UPLOAD_DIR / task_id
    output_path = OUTPUT_DIR / task_id
    if upload_path.exists():
        shutil.rmtree(str(upload_path), ignore_errors=True)
    if output_path.exists():
        shutil.rmtree(str(output_path), ignore_errors=True)
    return jsonify({"message": "已删除"})


@app.route("/api/history")
def get_history():
    """获取转换历史"""
    return jsonify(load_history())


@app.route("/api/history/<task_id>", methods=["DELETE"])
def delete_history(task_id):
    """删除历史记录"""
    records = load_history()
    records = [r for r in records if r.get("task_id") != task_id]
    save_history(records)
    return jsonify({"message": "已删除"})


# ==================== 管线转换 API ====================


def _validate_pipeline_path(dir_path):
    """校验管线目录路径，防止目录穿越"""
    resolved = Path(dir_path).resolve()
    # 路径必须存在且为目录
    if not resolved.is_dir():
        return None, "指定路径不存在或不是目录"
    # 禁止访问系统敏感目录
    sensitive = ["/etc", "/proc", "/sys", "/dev", "/boot",
                 "C:\\Windows", "C:\\Program Files"]
    for s in sensitive:
        if str(resolved).startswith(s):
            return None, "不允许访问系统目录"
    return str(resolved), None


@app.route("/api/pipeline-scan", methods=["POST"])
def pipeline_scan():
    """扫描管线目录，返回找到的 GUID 模型列表"""
    data = request.get_json(force=True)
    dir_path = data.get("path", "").strip()
    if not dir_path:
        return jsonify({"error": "请提供目录路径"}), 400
    safe_path, err = _validate_pipeline_path(dir_path)
    if err:
        return jsonify({"error": err}), 400
    result = scan_pipeline_directory(safe_path)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


def run_pipeline_conversion(task_id, dir_path, scan_result, options):
    """管线批量转换后台线程"""
    try:
        tasks[task_id]["status"] = "running"
        items = scan_result["items"]
        total = len(items)

        def log(msg):
            tasks[task_id]["log"] += msg + "\n"
            tasks[task_id]["message"] = msg

        # 1. 计算中心点
        log(f"共 {total} 个管线模型，正在计算中心点...")
        center = compute_center_origin(items)
        log(f"中心点 srsorigin: {center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f}")

        # 2. 坐标转换
        epsg_code = scan_result["epsg_list"][0] if scan_result["epsg_list"] else "EPSG:4326"
        try:
            lat, lon, alt = convert_to_wgs84(epsg_code, center)
            log(f"中心点 WGS84: lat={lat:.8f}, lon={lon:.8f}, alt={alt:.2f}")
        except Exception as e:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["message"] = f"坐标转换失败: {e}"
            return

        # 3. 合并 OBJ
        task_upload_dir = UPLOAD_DIR / task_id
        task_output_dir = OUTPUT_DIR / task_id
        task_upload_dir.mkdir(parents=True, exist_ok=True)
        task_output_dir.mkdir(parents=True, exist_ok=True)

        log("开始合并 OBJ 模型...")
        merged_obj = merge_pipeline_objs(items, center, str(task_upload_dir), log)
        log(f"合并文件: {merged_obj}")

        # 4. 调用 Obj2Tiles 转换
        exe = get_obj2tiles_path()
        if not exe:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["message"] = "Obj2Tiles 未找到"
            return

        cmd = [exe, merged_obj, str(task_output_dir)]
        cmd.extend(["--lat", str(lat), "--lon", str(lon), "--alt", str(alt)])

        lods = options.get("lods", 3)
        divisions = options.get("divisions", 2)
        cmd.extend(["--lods", str(lods), "--divisions", str(divisions)])

        if options.get("error"):
            cmd.extend(["--error", str(options["error"])])
        if options.get("scale"):
            cmd.extend(["--scale", str(options["scale"])])
        if options.get("split_strategy"):
            cmd.extend(["--split-strategy", options["split_strategy"]])
        if options.get("keep_textures"):
            cmd.append("--keeptextures")
        if options.get("zsplit"):
            cmd.append("--zsplit")
        if options.get("use_system_temp"):
            cmd.append("--use-system-temp")

        log(f"执行: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(task_upload_dir),
        )

        for line in process.stdout:
            line = line.strip()
            if line:
                tasks[task_id]["log"] += line + "\n"
                tasks[task_id]["message"] = line

        process.wait()

        if process.returncode != 0:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["message"] = f"Obj2Tiles 转换失败 (返回码: {process.returncode})"
            return

        tileset_path = task_output_dir / "tileset.json"
        if not tileset_path.exists():
            tasks[task_id]["status"] = "error"
            tasks[task_id]["message"] = "转换完成但未生成 tileset.json"
            return

        # 5. 旋转修正
        rx = options.get("rotate_x", -90)
        ry = options.get("rotate_y", 0)
        rz = options.get("rotate_z", 0)
        if rx or ry or rz:
            log("应用旋转修正...")
            apply_rotation_to_tileset(str(tileset_path), rx, ry, rz)

        # 6. 后处理
        pp_opts = {
            "draco": options.get("pp_draco"),
            "webp": options.get("pp_webp"),
            "ktx2": options.get("pp_ktx2"),
            "ktx2_mode": options.get("pp_ktx2_mode", "uastc"),
        }
        if any(pp_opts.values()):
            log("正在后处理...")
            run_postprocess(str(task_output_dir), pp_opts, log)

        tasks[task_id]["status"] = "done"
        tasks[task_id]["message"] = f"管线转换完成: {total} 个模型"
        tasks[task_id]["tileset_url"] = f"/output/{task_id}/tileset.json"
        append_history(
            {
                "task_id": task_id,
                "obj_name": f"管线批量 ({total} 个模型)",
                "mode": "pipeline",
                "status": "done",
                "tileset_url": tasks[task_id]["tileset_url"],
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["message"] = f"管线转换异常: {str(e)}"


@app.route("/api/pipeline-convert", methods=["POST"])
def pipeline_convert():
    """启动管线批量转换"""
    data = request.get_json(force=True)
    dir_path = data.get("path", "").strip()
    if not dir_path:
        return jsonify({"error": "请提供目录路径"}), 400

    safe_path, err = _validate_pipeline_path(dir_path)
    if err:
        return jsonify({"error": err}), 400

    scan_result = scan_pipeline_directory(safe_path)
    if "error" in scan_result:
        return jsonify(scan_result), 400
    if scan_result["count"] == 0:
        return jsonify({"error": "目录中未找到管线模型"}), 400

    options = data.get("options", {})
    task_id = str(uuid.uuid4())[:8]

    tasks[task_id] = {
        "status": "queued",
        "message": f"管线转换排队中... ({scan_result['count']} 个模型)",
        "log": "",
        "tileset_url": None,
        "obj_name": f"管线批量 ({scan_result['count']} 个)",
        "mode": "pipeline",
    }

    _task_pool.submit(run_pipeline_conversion, task_id, safe_path, scan_result, options)

    return jsonify(
        {
            "task_id": task_id,
            "task_ids": [task_id],
            "message": f"管线转换已启动: {scan_result['count']} 个模型",
        }
    )


if __name__ == "__main__":
    print(f"服务启动中...")
    print(f"上传目录: {UPLOAD_DIR}")
    print(f"输出目录: {OUTPUT_DIR}")
    print(f"工具目录: {TOOLS_DIR}")
    exe = get_obj2tiles_path()
    if exe:
        print(f"Obj2Tiles 路径: {exe}")
    else:
        print("⚠ Obj2Tiles 未找到！请下载并放入 tools/ 目录")
        print("  下载地址: https://github.com/OpenDroneMap/Obj2Tiles/releases")
    port = int(os.environ.get("PORT", 38020))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)

<div align="center">

# OBJ2Tiles

**高性能 OBJ 模型 → OGC 3D Tiles 转换引擎**

*跨平台 · 国产化适配 · 纹理压缩 · 几何压缩 · 零 Node.js 依赖*

[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask)](https://flask.palletsprojects.com)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20|%20Linux%20|%20麒麟%20|%20统信-blue)]()

</div>

---

## 概述

OBJ2Tiles 是一套面向 **生产环境** 的三维模型数据转换服务，将 Wavefront OBJ 格式高效转换为符合 [OGC 3D Tiles 1.0](https://www.ogc.org/standard/3dtiles/) 标准的瓦片数据集。内置 **Draco 几何压缩、WebP / KTX2 纹理压缩** 全链路后处理管线，整体技术栈为纯 Python 实现，**零 Node.js 运行时依赖**。

> 已在 **Windows 10/11、Ubuntu 20.04/22.04、银河麒麟 V10、统信 UOS V20** 环境验证通过。

### 截图

> TODO: 添加 Web UI 截图

---

## 核心特性

| 特性 | 说明 |
|:---|:---|
| 🔄 **OBJ → 3D Tiles** | 支持 Octree / Quadtree / K-d Tree 三种空间分割策略，自动 LOD 生成 |
| 🗜️ **Draco 几何压缩** | 纯 Python GLB 解析 + DracoPy 编码，输出符合 `KHR_draco_mesh_compression` 扩展 |
| 🖼️ **WebP 纹理压缩** | Pillow 内存转码，GLB 纹理原地替换，体积缩减 60%–80% |
| 📦 **KTX2 纹理压缩** | UASTC（高质量 + Zstd 超级压缩）/ ETC1S（极限压缩）双模式，符合 `KHR_texture_basisu` 扩展 |
| ⚡ **批量并行转换** | 多 OBJ 文件同时上传，线程池并行处理（可配置并发数） |
| 🌍 **地理定位** | 经度 / 纬度 / 高程参数化定位，支持 X/Y/Z 轴旋转修正 |
| 🗺️ **管线模型转换** | 支持 GUID 管线目录批量扫描、投影坐标自动转 WGS84、多模型合并 |
| 🖥️ **Web 可视化** | 内置 CesiumJS 三维预览，转换完成即可在线查看 |
| 📡 **离线部署** | 自带全量离线依赖包（Python wheels + 二进制工具），内网环境开箱即用 |

---

## 平台兼容性

| 操作系统 | 架构 | 状态 |
|:---|:---|:---|
| Windows 10 / 11 | x86_64 | ✅ 已验证 |
| Ubuntu 20.04 / 22.04 | x86_64 | ✅ 已验证 |
| 银河麒麟 V10 (Kylin) | x86_64 / aarch64 | ✅ 已验证 |
| 统信 UOS V20 (Uniontech) | x86_64 / aarch64 | ✅ 已验证 |
| CentOS 7 / 8 | x86_64 | ✅ 已验证 |

---

## 快速开始

### Windows

```bash
# 克隆仓库
git clone https://github.com/zhanghaojojo/obj2tiles.git
cd obj2tiles

# 安装依赖
pip install -r requirements.txt

# 启动服务
python app.py

# 浏览器访问 http://localhost:38020
```

### Linux / 麒麟 / 统信

```bash
# 方式一：在线环境
pip3 install -r requirements.txt
python3 app.py

# 方式二：离线部署（推荐生产环境）
cd deploy-linux
chmod +x start.sh stop.sh tools/Obj2Tiles
./start.sh     # 自动创建 venv → 离线安装依赖 → 启动服务
./stop.sh      # 停止服务
```

> **离线部署包** 内含全量 Python wheels 和预编译二进制工具，无需外网访问。

---

## 技术架构

```
┌──────────────────────────────────────────────────────┐
│                   Web UI (CesiumJS)                  │
├──────────────────────────────────────────────────────┤
│                  Flask REST API                      │
├──────────┬──────────┬──────────┬─────────────────────┤
│ OBJ 解析 │ 空间分割  │ LOD 生成 │   tileset.json 输出 │
│          │ Octree   │          │                     │
│          │ Quadtree │          │                     │
│          │ K-d Tree │          │                     │
├──────────┴──────────┴──────────┴─────────────────────┤
│               后处理管线 (Pure Python)                 │
│  ┌─────────┐  ┌──────────┐  ┌──────────────────┐    │
│  │  Draco   │  │  WebP    │  │  KTX2            │    │
│  │ 几何压缩 │→ │ 纹理压缩 │→ │ UASTC / ETC1S   │    │
│  └─────────┘  └──────────┘  └──────────────────┘    │
├──────────────────────────────────────────────────────┤
│           Obj2Tiles 核心引擎 (跨平台二进制)            │
└──────────────────────────────────────────────────────┘
```

### 技术栈

| 层级 | 组件 | 技术选型 |
|:---|:---|:---|
| Web 框架 | REST API + 静态服务 | Flask 3.x + flask-cors |
| 转换引擎 | OBJ → B3DM/GLB 瓦片 | Obj2Tiles (跨平台二进制) |
| 几何压缩 | Draco 网格编码 | DracoPy + NumPy |
| 纹理压缩 | WebP 转码 | Pillow (内存管线) |
| 纹理压缩 | KTX2 / Basis Universal | Pillow + toktx (KTX-Software) |
| 坐标转换 | 投影坐标 → WGS84 → ECEF | pyproj |
| 三维预览 | 在线瓦片可视化 | CesiumJS 1.119 |

---

## API 文档

### 通用转换

| 方法 | 端点 | 说明 |
|:---|:---|:---|
| `POST` | `/api/upload` | 上传 OBJ 文件或 ZIP 包，启动转换任务 |
| `GET` | `/api/status/<task_id>` | 查询转换任务状态与日志 |
| `GET` | `/api/tasks` | 列出所有任务 |
| `DELETE` | `/api/delete/<task_id>` | 删除任务及关联文件 |
| `GET` | `/api/history` | 获取转换历史记录 |
| `DELETE` | `/api/history/<task_id>` | 删除指定历史记录 |
| `GET` | `/api/check-tool` | 检测 Obj2Tiles 引擎状态 |
| `GET` | `/output/<path>` | 静态资源访问（3D Tiles 瓦片） |

### 管线转换

| 方法 | 端点 | 说明 |
|:---|:---|:---|
| `POST` | `/api/pipeline-scan` | 扫描管线 GUID 目录 |
| `POST` | `/api/pipeline-convert` | 启动管线批量转换 |

### 转换参数

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `lat` / `lon` / `alt` | float | 地理定位：纬度 / 经度 / 高程 |
| `split_strategy` | string | 空间分割策略：`octree` / `quadtree` / `kdtree` |
| `lods` | int | LOD 层级数 |
| `scale` | float | 模型缩放系数 |
| `rotate_x/y/z` | float | 轴向旋转角度（度） |
| `pp_draco` | bool | 启用 Draco 几何压缩 |
| `pp_webp` | bool | 启用 WebP 纹理压缩 |
| `pp_ktx2` | bool | 启用 KTX2 纹理压缩 |
| `pp_ktx2_mode` | string | KTX2 模式：`uastc`（高质量）/ `etc1s`（高压缩） |

---

## 压缩效果参考

| 压缩方式 | 典型压缩率 | 质量影响 | GPU 解码 |
|:---|:---|:---|:---|
| Draco 几何压缩 | 70%–90% 体积缩减 | 可控精度损失 | ✅ 硬件加速 |
| WebP 纹理压缩 | 60%–80% 体积缩减 | 接近无损 | ❌ CPU 解码 |
| KTX2 UASTC | 50%–70% 体积缩减 | 高质量 | ✅ GPU 直接采样 |
| KTX2 ETC1S | 80%–95% 体积缩减 | 有损（适合远景） | ✅ GPU 直接采样 |

---

## 目录结构

```
obj2tiles/
├── app.py                  # Flask 主程序 & REST API
├── postprocess.py          # 后处理引擎（Draco / WebP / KTX2 纯 Python 管线）
├── pipeline_convert.py     # 管线模型处理（坐标转换、OBJ 合并）
├── pack.py                 # 离线部署包打包脚本
├── requirements.txt        # Python 依赖
├── templates/
│   └── index.html          # Web UI（CesiumJS 集成）
├── tools/                  # Windows Obj2Tiles 二进制
├── tools_linux/            # Linux Obj2Tiles 二进制
├── vendor/                 # 离线依赖（Python wheels 等）
├── deploy-linux/           # Linux / 国产化完整离线部署包
│   ├── start.sh / stop.sh  # 服务管理脚本
│   └── vendor/             # 离线 wheels（Python 3.7–3.12）
├── uploads/                # 上传文件暂存（运行时）
└── output/                 # 3D Tiles 输出（运行时）
```

---

## 依赖说明

### Python 包

| 包名 | 版本范围 | 用途 |
|:---|:---|:---|
| Flask | >=3.0, <4 | Web 框架 |
| flask-cors | >=4.0, <5 | 跨域支持 |
| Pillow | >=10.0, <12 | 图像处理（WebP / KTX2 纹理转换） |
| NumPy | >=1.24, <3 | 数值计算（Draco 网格数据处理） |
| DracoPy | >=1.4, <2 | Draco 几何压缩编码 |
| pyproj | >=3.6, <4 | 坐标系转换 |

### 外部工具（已内置）

| 工具 | 版本 | 用途 |
|:---|:---|:---|
| Obj2Tiles | 1.4.0 | OBJ 模型瓦片化核心引擎 |
| toktx (KTX-Software) | 4.4.2 | KTX2 / Basis Universal 纹理编码（可选） |

---

## 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'feat: add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

---

## 作者

**张二狗** — 野生程序员 🐕

一个在 GIS 和三维可视化领域摸爬滚打的独立开发者。白天搬砖，晚上写码，周末调 Bug。

- 擅长：三维数据处理、WebGIS、跨平台工程化
- 状态：永远在 debug 的路上

---

## License

[MIT License](LICENSE)

Copyright © 2024 张二狗

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

# -*- mode: python ; coding: utf-8 -*-
"""打包脚本：python build.py

产物是「绿色包」：dist/AI号池管理器/ 整个文件夹拷走即可用，
里面只有一个 exe 需要双击，其余是运行库和内置的 cli-proxy-api.exe。

注意别用 build.spec 这种名字：PyInstaller 会用 spec 文件名生成中间目录
（build/build/），里面那个 exe 缺 _internal，双击就报 Failed to load Python DLL。
"""

HIDDEN = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "h11",
    "anyio._backends._asyncio",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[("vendor/cli-proxy-api.exe", "vendor")],
    datas=[
        ("web", "web"),
        ("vendor/modeltrace", "vendor/modeltrace"),
    ],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PySide6", "PyQt5"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI号池管理器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="app.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AI号池管理器",
)

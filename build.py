"""一键重建绿色包：python build.py

做四件事：
  1. 把上一次的产物挪到项目上一级 .build-trash（同盘改名）
  2. 调 PyInstaller 打包
  3. 把说明书放进包里
  4. 删掉 build 目录里那个缺 _internal、双击会报 DLL 错的中间 exe
最后可选打 zip。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import time
import tarfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
NAME = "AI号池管理器"
SPEC = ROOT / f"{NAME}.spec"
DIST_ROOT = ROOT / "dist"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
DIST = DIST_ROOT / (f"{NAME}-linux" if IS_LINUX else (f"{NAME}-macOS" if IS_MAC else NAME))
WORK = ROOT / "build" / NAME
PYINSTALLER_DIST = WORK / "dist" if IS_MAC else (DIST if IS_LINUX else DIST_ROOT)
DOCS = ["使用说明.md", "先看这里.txt"]


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def _retire_old_dist() -> pathlib.Path | None:
    """旧产物改名挪开。

    两个坑都踩过，所以这里必须这么写：
      · 一次删上百个文件会触发批量删除保护，整个脚本直接中断；
      · 挪到系统临时目录会跨盘符（本项目在 C:，TEMP 常在 D:），rename 会抛 WinError 17。
    所以固定挪到「项目上一级/.build-trash/」——同盘符、瞬时就够了，之后再去回收站。
    """
    if IS_MAC or IS_LINUX:
        archive_name = (f"{NAME}-linux-gui-{platform.machine()}.tar.gz" if IS_LINUX
                        else f"{NAME}-macos-{platform.machine()}.zip")
        candidates = [DIST, DIST_ROOT / archive_name]
        old_paths = [path for path in candidates if path.exists() or path.is_symlink()]
        if not old_paths:
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns()}"
        trash = (ROOT / ".build-trash" if IS_LINUX else ROOT.parent / ".build-trash") / stamp
        trash.mkdir(parents=True, exist_ok=True)
        for path in old_paths:
            path.rename(trash / path.name)
        log(f"旧产物已挪到 {trash}")
        return trash

    if not DIST.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    trash = ROOT.parent / ".build-trash" / stamp
    trash.parent.mkdir(parents=True, exist_ok=True)
    DIST.rename(trash)          # 同盘符 rename，失败就让异常冒出去，别硬着头皮覆盖
    log(f"旧产物已挪到 {trash}")
    return trash


def _recycle(path: pathlib.Path) -> bool:
    """扔进回收站（不是硬删），清不掉就算了。"""
    if os.name != "nt":
        try:
            shutil.rmtree(path)
            return True
        except Exception:
            return False

    import ctypes
    from ctypes import wintypes

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION = 3, 0x0004, 0x0010
    FOF_ALLOWUNDO, FOF_NOERRORUI = 0x0040, 0x0400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = str(path.resolve()) + "\0\0"
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    try:
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0
    except Exception:
        return False


def _run_pyinstaller() -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller", str(SPEC),
        "--noconfirm",
        "--workpath", str(WORK),
        "--distpath", str(PYINSTALLER_DIST),
    ]
    log("开始打包…")
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(f"PyInstaller 失败，退出码 {r.returncode}")


def _prepare_mac_bundle() -> None:
    app = PYINSTALLER_DIST / f"{NAME}.app"
    if not app.is_dir():
        raise SystemExit(f"未生成预期应用：{app}")
    DIST.mkdir(parents=True, exist_ok=True)
    app = pathlib.Path(shutil.move(str(app), str(DIST / app.name)))
    try:
        subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(app)], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        log(f"临时签名失败（可忽略）：{exc}")


def _copy_docs() -> None:
    for fn in DOCS + (["macOS说明.md"] if IS_MAC else []) + (["Linux桌面版说明.md"] if IS_LINUX else []):
        src = ROOT / "docs" / fn
        if src.exists():
            shutil.copy2(src, DIST / fn)
            log(f"已放入 {fn}")


def _copy_licenses() -> None:
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, DIST / name)
    shutil.copytree(ROOT / 'licenses', DIST / 'licenses', dirs_exist_ok=True)


def _drop_build_exe() -> None:
    """build/<name>/<name>.exe 是 EXE 阶段的中间产物，缺 _internal 跑不起来，
    留着容易被人误点，直接删掉。"""
    stale = WORK / f"{NAME}.exe"
    if stale.exists():
        try:
            stale.unlink()
            log("已清理中间产物里的 exe")
        except Exception as e:      # 被占用就提示一下，不影响交付
            log(f"中间 exe 清理失败（可忽略）：{e}")


def _drop_trash(trash: pathlib.Path | None) -> None:
    """构建成功后再把旧产物扔进回收站；失败也不影响交付。"""
    if not trash:
        return
    if _recycle(trash):
        log("旧产物已放入回收站")
    else:
        log(f"旧产物留在 {trash}，不影响本次构建")


def _make_zip(out: pathlib.Path) -> None:
    if out.exists():
        out.unlink()
    if IS_LINUX:
        with tarfile.open(out, "w:gz", dereference=False) as archive:
            archive.add(DIST, arcname=DIST.name)
        log(f"已生成 {out}（{out.stat().st_size / 1024 / 1024:.1f} MB）")
        return
    if IS_MAC:
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(DIST), str(out.resolve())], check=True)
        log(f"已生成 {out}（{out.stat().st_size / 1024 / 1024:.1f} MB）")
        return
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(DIST.rglob("*")):
            z.write(p, pathlib.Path(NAME) / p.relative_to(DIST))
    log(f"已生成 {out}（{out.stat().st_size / 1024 / 1024:.1f} MB）")


def main() -> int:
    ap = argparse.ArgumentParser(description="重建 AI 号池管理器绿色包")
    ap.add_argument("--no-zip", action="store_true", help="不生成压缩包（Linux 为 tar.gz）")
    ap.add_argument("--zip", default="", help="压缩包输出路径；Linux 为 tar.gz，macOS/Windows 为 zip")
    args = ap.parse_args()

    if not SPEC.exists():
        raise SystemExit(f"找不到 {SPEC.name}")

    if (IS_MAC or IS_LINUX) and not (ROOT / "vendor" / "cli-proxy-api").is_file():
        raise SystemExit("缺少 vendor/cli-proxy-api，请先运行 bash tools/fetch_cli_proxy_api.sh")
    trash = _retire_old_dist()
    _run_pyinstaller()
    if IS_MAC:
        _prepare_mac_bundle()
    if IS_LINUX:
        for filename in ("aipool-manager.desktop", "install-desktop.sh"):
            shutil.copy2(ROOT / "packaging" / "linux" / filename, DIST / filename)
        shutil.copy2(ROOT / "web" / "logo.png", DIST / "aipool-manager.png")
        (DIST / "install-desktop.sh").chmod(0o755)
    _copy_docs()
    _copy_licenses()
    if not IS_MAC and not IS_LINUX:
        _drop_build_exe()
    _drop_trash(trash)

    if not args.no_zip:
        archive_name = (f"{NAME}-linux-gui-{platform.machine()}.tar.gz" if IS_LINUX
                        else (f"{NAME}-macos-{platform.machine()}.zip" if IS_MAC else f"{NAME}.zip"))
        out = pathlib.Path(args.zip) if args.zip else DIST_ROOT / archive_name
        _make_zip(out)

    log("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())

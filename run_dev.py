"""本地开发启动器：编译主进程 → 起 Vite → 开 Electron 窗口。

Electron 主进程在开发模式下要求 http://localhost:5173 有 Vite 服务，
但 package.json 的 dev 脚本只做了构建、没起服务，所以这里补上。
窗口关掉后会自动结束 Vite 进程。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
ELECTRON = FRONTEND / "node_modules" / "electron" / "dist" / "electron.exe"
VITE = FRONTEND / "node_modules" / ".bin" / "vite.cmd"
DEV_URL = "http://localhost:5173"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def say(msg: str) -> None:
    print(msg, flush=True)


def find_npm() -> tuple[str | None, Path | None]:
    """找 npm.exe/npm.cmd，并返回需要补进 PATH 的目录。

    双击 bat 启动时拿到的是系统 PATH，里面往往没有 Node ——
    尤其是 Node 装在用户目录、或用的是 WorkBuddy 自带的那份。
    所以除了 PATH，还要翻常见安装位置。
    """
    for name in ("npm.cmd", "npm.exe", "npm"):
        found = shutil.which(name)
        if found:
            return found, Path(found).parent

    candidates: list[Path] = []
    # WorkBuddy 自带 Node（这台机器只装了这份）
    versions = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if versions.is_dir():
        candidates.extend(sorted(versions.glob("*/npm.cmd"), reverse=True))
    for base in filter(None, (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"))):
        candidates.append(Path(base) / "nodejs" / "npm.cmd")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "nodejs" / "npm.cmd")
    roaming = os.environ.get("APPDATA")
    if roaming:
        candidates.append(Path(roaming) / "npm" / "npm.cmd")
        candidates.extend(sorted((Path(roaming) / "nvm").glob("v*/npm.cmd"), reverse=True))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate), candidate.parent
    return None, None


def fail(msg: str, code: int = 1) -> None:
    print("\n[错误] " + msg, flush=True)
    try:
        input("按回车退出...")
    except EOFError:
        pass
    raise SystemExit(code)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 老环境没有 reconfigure
        pass

    if not VENV_PY.exists():
        fail(
            f"找不到虚拟环境：{VENV_PY}\n"
            "请先在项目根目录执行：\n"
            "    python -m venv .venv\n"
            "    .venv\\Scripts\\python.exe -m pip install -r backend\\requirements.txt"
        )
    if not ELECTRON.exists():
        fail("没找到 Electron，请先在 frontend 目录执行：npm install")
    if not VITE.exists():
        fail("没找到 Vite，请先在 frontend 目录执行：npm install")

    npm, npm_bin = find_npm()
    if not npm:
        fail(
            "找不到 npm。这个项目需要 Node.js：\n"
            "  1) 装一个 Node.js 18 或更新版本（https://nodejs.org），装完重开本窗口\n"
            "  2) 已经装了但可能是没进 PATH：新开一个命令行执行 node -v，\n"
            "     能看到版本号才算正常；看不到就去「系统属性 → 环境变量」把 Node 加进 PATH"
        )

    env = os.environ.copy()
    # 有 ELECTRON_RUN_AS_NODE，Electron 会退化成纯 Node，主进程直接崩
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env["NODE_ENV"] = "development"
    env["VIDEOMATRIX_PYTHON"] = str(VENV_PY)
    # npm.cmd / electron 都要能调到 node
    if npm_bin:
        env["PATH"] = str(npm_bin) + os.pathsep + env.get("PATH", "")
    if not shutil.which("node", path=env["PATH"]):
        fail(f"找到了 npm（{npm}），但同目录没有 node.exe，安装可能不完整。")

    say("[1/3] 编译 Electron 主进程...")
    for target in ("build:preload", "build:main"):
        result = subprocess.run([npm, "run", target], cwd=FRONTEND, env=env)
        if result.returncode != 0:
            fail(f"构建失败（{target}），请查看上面的输出")

    say("[2/3] 启动界面服务...")
    vite_log = FRONTEND / "vite-dev.log"
    log = open(vite_log, "w", encoding="utf-8", errors="replace")
    vite = subprocess.Popen(
        [str(VITE), "--config", "vite.renderer.config.ts",
         "--port", "5173", "--strictPort"],
        cwd=FRONTEND,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )

    say("[3/3] 等待界面就绪...")
    ready = False
    for _ in range(40):
        if vite.poll() is not None:
            break
        try:
            with urllib.request.urlopen(DEV_URL, timeout=1.5):
                ready = True
                break
        except Exception:  # noqa: BLE001 - 服务还没起来
            time.sleep(0.5)

    if not ready:
        vite.terminate()
        log.close()
        fail(f"界面服务没能启动，日志见：{vite_log}")

    say("启动主窗口...（有定时任务时关窗口会收进托盘）")
    try:
        subprocess.run(
            # --disable-gpu: 本机 GPU 进程会崩，走软件渲染
            # --no-sandbox: 部分环境（远程会话/受限账户）下渲染进程会因沙箱崩溃白屏
            [str(ELECTRON), ".", "--disable-gpu", "--disable-gpu-sandbox", "--no-sandbox", "--disable-dev-shm-usage"],
            cwd=FRONTEND,
            env=env,
        )
    except KeyboardInterrupt:
        pass
    finally:
        vite.terminate()
        try:
            vite.wait(timeout=5)
        except subprocess.TimeoutExpired:
            vite.kill()
        log.close()

    say("窗口已关闭。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

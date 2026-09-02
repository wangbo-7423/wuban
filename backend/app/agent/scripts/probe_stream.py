# -*- coding: utf-8 -*-
"""SSE 流式探针：打 /api/student/chat/stream，记录每帧事件的到达时间。

目的：判断「思考很久 → 一口气全输出」的体感究竟来自
  a) 模型/网关真的没流式（所有帧同一时刻到达），还是
  b) 流式正常但发生在思维链里、正文期太短（帧陆续到达）。
"""
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000"


def post_json(path: str, payload: dict, token: str | None = None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def main() -> None:
    ts = int(time.time())
    username, password = f"streamprobe_{ts}", "probe123456"

    # 1) 注册（自动登录，返回 token）
    with urllib.request.urlopen(
        post_json(
            "/api/auth/register",
            {"username": username, "nickname": "流式探针", "password": password},
        )
    ) as resp:
        body = json.loads(resp.read())
    token = body["data"]["token"]
    print(f"[ok] 注册探针账号 {username}，拿到 token", flush=True)

    # 2) 打流式端点，逐帧记时间
    prompt = "用 300 字左右解释一下：为什么天空是蓝色的？"
    req = post_json(
        "/api/student/chat/stream",
        {"message": prompt, "course_id": "general"},
        token,
    )
    t0 = time.perf_counter()
    print(f"[ok] 发送问题：{prompt}\n", flush=True)
    print(f"{'t(s)':>8}  {'gap(ms)':>8}  {'event':<10} {'chars':>6}  preview", flush=True)
    print("-" * 78, flush=True)

    last_t = t0
    counts: dict[str, int] = {}
    first_of: dict[str, float] = {}
    last_of: dict[str, float] = {}

    with urllib.request.urlopen(req) as resp:
        buf = b""
        while True:
            chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                now = time.perf_counter()
                event, data = "message", ""
                for line in frame.decode("utf-8", "replace").split("\n"):
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data += line[5:].strip()
                if not data:
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    payload = {}
                piece = payload.get("delta") or payload.get("text") or ""
                n = len(piece) if isinstance(piece, str) else len(data)
                counts[event] = counts.get(event, 0) + 1
                first_of.setdefault(event, now - t0)
                last_of[event] = now - t0
                preview = (piece or json.dumps(payload, ensure_ascii=False))[:40].replace("\n", " ")
                print(
                    f"{now - t0:8.2f}  {(now - last_t) * 1000:8.0f}  {event:<10} {n:>6}  {preview}",
                    flush=True,
                )
                last_t = now

    print("\n===== 汇总 =====")
    for ev, c in counts.items():
        print(f"{ev:<10} 帧数={c:<5} 首帧 t={first_of[ev]:6.2f}s  末帧 t={last_of[ev]:6.2f}s")
    total = last_of.get("done", time.perf_counter() - t0)
    print(f"总耗时 ≈ {total:.2f}s")


if __name__ == "__main__":
    sys.exit(main())

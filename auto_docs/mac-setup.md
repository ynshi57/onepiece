# 本机 VQASee / OnePiece 安装纪要

## 最终结论

这台 Intel Mac（macOS 14.6.1，Core i5 2GHz / 16GB / Iris Plus 核显）上，**core + models + qwen 已装好并能端到端跑通**。Homebrew `openssl@3` **3.6.4** 与 keg-only `ruby@3.4` **3.4.10** 已装好。iOS 完整构建仍缺 Simulator runtime / Apple 签名。

本地推理是 **纯 CPU**。行走模式一帧约 **20s**（不是 README 里 M4 Air 的 ~2.5s）。

## 方案要点

- Python 用 **3.11.1** 建 `.venv`（系统默认 `python3` 是 3.9.12，不够用）。
- `numpy` / `Pillow` 提升为 `requirements.txt` 硬依赖（`app.main` 加载即导入）。
- Intel Mac 上 torch 封顶 2.2.2，必须 **numpy 1.x** 才能转 Core ML。
- Ollama 0.33.3 手工安装（brew tap 冻结，cask 找不到 ollama）。
- Qwen 3B greedy decode 会在 `changes` 字段死循环；默认加 `repeat_penalty=1.1`。

## 已执行

- venv + 依赖 + `pytest` **293 passed**
- perception-harness 已编译
- Core ML：YOLO11n、车道分割、mc5、通行分割（新转）均在位
- `qwen2.5vl:3b` 3.2GB 已拉取
- llama-server `:11435` + 后端 `:9000` 在跑
- 真实街景帧 WebSocket 验证 **2/2 成功**，约 20s，JSON 合法
- Homebrew 已迁回 `main`（6.0.22）；Portable Ruby 4.0.6_2、`openssl@3` 3.6.4、`ruby@3.4` 3.4.10 已装
- `~/.zshrc` 已把 `/usr/local/opt/ruby@3.4/bin` 提前；新开终端后 `ruby -v` 应为 3.4.10
- 过期 CLT（clang 12）已删，改为指向 Xcode 16.2 的 clang/SDK 桩，供 brew 源码编译用

## 阻塞 / 待办

- **iOS**：fastlane `bundle install` 未跑；仍缺 iOS Simulator runtime、Apple 签名账号。Xcode 16.2 与 `VQASee.xcodeproj` 已在。
- 后端启动 warmup 对 1x1 PNG 返回 400（非致命；llama-server 自己的 warmup 已成功）。
- 本机 HTTPS_PROXY=`127.0.0.1:8118` 会截断 formulae.brew.sh 大 JSON、拖死 ghcr.io。brew 请直连并设：
  `HOMEBREW_API_DOMAIN` / `HOMEBREW_BOTTLE_DOMAIN` = `https://mirrors.ustc.edu.cn/homebrew-bottles[/api]`。

## 关键路径

```text
iPhone / 验证脚本 → ws://127.0.0.1:9000/ws/signaling
                 → llama-server :11435  qwen2.5vl:3b
```

iPhone 同网：`ws://192.168.1.82:9000/ws/signaling`（以当时 LAN IP 为准）。

## 风险与回滚

- CPU 20s/帧，不适合当实时体验；换 Apple Silicon 或接受门控少推理。
- `repeat_penalty` 设 `QWEN_REPEAT_PENALTY=1.0` 可关；关掉后行走帧会再次截断成「模型输出异常」。
- 通行分割 mlmodelc 在 `.gitignore`，换机需重跑 models profile。

更新时间: 2026-09-09 23:58

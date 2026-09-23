# 真身评估卡在 12/12

## 最终结论

感知已经跑完。`/tmp/camvid-manifest-drive-test-ios-harness.exit` 是 0，jsonl 12 行，stderr 有 `harness done`。页面卡住是因为包装 `bash` 变成僵尸进程（pid 40764 `STAT=Z`），`os.kill(pid, 0)` 仍成功，平台以为还在跑。

## 根因

诊断台用 `Popen(..., start_new_session=True)` 启动 bash，父进程是 uvicorn worker，从不 `wait()`。harness 退出后 bash 变成僵尸，锁不释放。进度文案在 remaining=0 时仍用 12 帧估时，所以显示「剩余约 1 分钟」。

## 已执行

- `exit` 文件存在则收结果，不等僵尸消失
- `_pid_is_running` 视 `Z` 为已结束，并 `waitpid(WNOHANG)` 收尸
- 启动后用 daemon 线程 `proc.wait()`，避免再留僵尸
- 写满帧后文案改为「已写完，正在收尾」，不再说还剩 1 分钟

## 验证

- `pytest` 覆盖僵尸 / exit 文件 / 进度文案
- 本机锁文件应在下次刷新时清掉，进入评估

更新时间: 2026-09-22 16:08

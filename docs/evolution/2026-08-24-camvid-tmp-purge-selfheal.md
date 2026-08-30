# 2026-08-24 真身感知全帧失败：/tmp 数据集被系统清理 → 自愈下载 + 诚实诊断 + 数据集持久化

## 现象

用户在诊断台点「一键在本机跑真身感知」，结果：

```
harness done: predicted=0 missing_image=0 decode_errors=701
decode_failed: /private/tmp/vqasee-open-datasets/camvid/CamVid-main/CamVid_RGB/Seq05VD_f04920.png
...（701 行 decode_failed）
```

UI 只显示「运行结束但未产出预测……见下方 stderr」，一屏 `decode_failed`，把人引向「图片损坏 / 解码器坏了」的错误方向。

## 根因（三层叠加）

1. **真因 · 数据集存在临时目录被系统清理**：开源数据集下载到 `/tmp/vqasee-open-datasets`。macOS 会周期性清理 `/private/tmp`（约 3 天未访问的文件被删）。数据集 8/18 下载，8/23–24 图片文件被 purge，只剩空的 `CamVid_RGB/` 等目录。→ 这是「不允许静默腐化」的典型反例。

2. **下载不自愈**：`/datasets/download-open` 用 `_find_dataset_dir(root, "CamVid_RGB") is None` 判断是否已下载，而 `_find_dataset_dir` 只检查**目录存在**、不检查**里面有没有文件**。空目录被当成「已就绪」→ 重复点也**静默跳过重新下载**。

3. **harness 误报**：`resolveImagePath` 只判断路径字符串非空、**不校验文件存在**；文件没了就被 `makePixelBuffer`（`CGImageSourceCreateWithURL` 对不存在文件返回 nil）记成 `decode_error`。把「文件缺失」误标成「解码失败」，误导排查。

## 修复

| 层 | 文件 | 改动 |
|----|------|------|
| 根因 | `server-vqa/app/diagnostic_api.py` `_open_dataset_root()` | 默认根从 `/tmp/vqasee-open-datasets` 迁到 `~/.cache/vqasee/open-datasets`（非临时，不会被系统清理）；若 legacy `/tmp` 里仍有图片则沿用，平滑过渡。仍支持 `VQASEE_DATASET_ROOT` 覆盖。 |
| 自愈 | 同上 新增 `_dir_has_images()` | 「目录存在但为空」== 未就绪。下载门禁、`_detect_camvid_dirs` 自动填充、下载后校验全部改用它 → 被清空后**自动重新下载**，不再静默跳过。 |
| 诚实诊断 | `ios-vqa-app/perception-harness/Sources/PerceptionHarness/main.swift` | 解码前先 `FileManager.fileExists`；缺文件计入 `missing_image` 并打印 `image_not_found:`，与 `decode_failed:` 明确区分。 |
| 可恢复文案 | `diagnostic_api.py` 真身运行结果 | stderr 含 `image_not_found:` 时，给出「数据集可能被系统清理，请回到接入开源数据集重新下载」的可执行指引，而不是干瘪的「为空」。 |
| 文案 | `diagnostic_api.py` 接入数据集页 hint | 去掉硬编码 `/tmp`，说明落在 `~/.cache/vqasee`（非临时）、可用 `VQASEE_DATASET_ROOT` 覆盖、被清空会自动重下。 |

## 验证（均已真机 / 测试验证）

- **自愈下载**：直接调修复后的 `dataset_download_open` → `resolved dataset root = ~/.cache/vqasee/open-datasets`（正确落到持久目录），下载 701 行、manifest 重生成、`CamVid_RGB has_images = True`。
- **harness 冒烟**：20 帧 → `predicted=20 missing_image=0 decode_errors=0`。
- **端到端**：`dataset_ios_harness_run(force=True)` 全量 → `status=ok, predicted=701`。
- **回归测试**：`test_dir_has_images_treats_empty_dir_as_absent`、`test_detect_camvid_dirs_ignores_empty_purged_dirs`；全量 `pytest server-vqa/tests` → **210 passed**。
- **harness 重编译**：`swift build` 成功（含 `FileManager.fileExists` 改动）。

## 经验

1. **闭环的输入数据必须持久化**：数据集是可复现闭环的输入，放临时目录 = 每隔几天静默失效。凡「跑一次就该稳定复用」的东西不进 `/tmp`。
2. **「目录存在」≠「就绪」**：判断资源就绪要看内容（有没有文件），不能只看文件夹在不在，否则自愈逻辑形同虚设。
3. **失败标签要诚实**：把「文件缺失」和「解码失败」混成一类，会把排查引向完全错误的方向。诊断信息的准确性本身就是安全/效率的一部分（呼应「不允许静默失败」）。
4. **一个问题发生两次就是系统没学会**：本轮把三层问题都用测试锁死，并把数据集搬离临时目录，避免复发。

## 后续修正：迁移目录的连带副作用（allowlist 脱节）

把数据集根从 `/tmp` 迁到 `~/.cache/vqasee` 后，用户点开帧图片报 `{"detail":"file_not_allowed"}`。原因：文件服务白名单 `_allowed_local_roots()` 只信任 `cwd` / `/tmp` / `/private/tmp` / 显式 `VQASEE_DATASET_ROOT`，**没包含新的持久默认根**。

修复：让 `_allowed_local_roots()` 追加 `_open_dataset_root()`，白名单与数据集实际落盘位置**始终同源** —— 以后再挪根也不会静默拆掉图片服务。补测试 `test_allowed_local_roots_track_the_dataset_root`、`test_local_file_serves_image_from_durable_dataset_root`；真机确认实图 `~/.cache/vqasee/.../0001TP_006690.png` 通过白名单；`pytest server-vqa/tests` → **212 passed**。

教训补充：**改动"资源位置"这类横切默认值时，要一次性排查所有依赖它的关卡**（下载门禁、自动填充、文件服务白名单、文案），否则修好一个入口却在另一个入口静默失败。

## 未做 / 边界

- harness 输出仍写 `/tmp/{stem}-ios-harness.jsonl`：输出是**派生物**、按需重算、且有内容哈希缓存，放 /tmp 可接受，未迁移。
- Swift 侧「文件缺失 vs 解码失败」目前无自动化测试（harness 无测试 target）；已用真机运行验证。若后续建 Swift 测试 target，应补一条 fixture。

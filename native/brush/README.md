# Brush本地实验

固定官方源码版本：`3edecbb2fe79d3e2c87eeab85b15e0b1dd10d486`（v0.3.0，Apache-2.0）。`source-manifest.json` 记录164份构建所需官方文件的Git blob身份，包括编译期`include_bytes!`需要的LPIPS权重及其完整SHA256。本目录保留VQASee补丁；可选安装入口为`setup_mac.sh --with-spatial-brush`，默认关闭，源码与产物进入仓库已忽略的`server-vqa/models/spatial-brush/`。`install_brush.sh --check-only`只核对本机，不联网。

`0001-small-image-header.patch` 修复 `get_image_data` 对合法小图片的读取错误：原版用read_exact要求读满16387字节，小于该大小的完整JPEG会UnexpectedEof，尚未尝试解码。改用read允许先解码实际可用的头部；真正空文件或无法解码的截断文件仍失败。

这是读取修复，不补齐/填充照片、不删除样本、不放宽训练或几何门槛。源文件其他内容保持官方实现。数据输出采用显式transforms_train/val，防止官方loader忽略transforms_eval而把评测丢失。模型导出和评测产物必须逐一核对；官方警告后继续或exit0均不能自动表示成功。

修补后 Intel Metal 已完成真实20步训练与13张独立留出渲染；更长的5000步训练也已执行，但画面仍模糊，尚未达到房间几何或清晰纹理验收。安装器的完整可选profile必须同时验证源码、构建、回执和可执行性；仅有既存二进制不代表新机安装通过。

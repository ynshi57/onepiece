# 照片竖直线与 ARKit 地面融合

日期：2026-10-15。扫描：咖啡店玻璃围合，导入记录 `~/.cache/vqasee/spatial-scans/db7a5089-c9b8-4a61-a8d9-df20cf4e2dd1`，97 张关键帧，竖面 4 块。

理论：Criminisi、Reid、Zisserman，Single View Metrology，IJCV 2000。度量地面和重力已知时，图像竖直线的地面接触点给出墙上一点的平面位置。线段本身只确定过相机的竖直平面，不定距离。玻璃中部没有接触点，射线会落到地面延长面上。

做法：原生 1920×1440 上做 LSD，方向对齐重力消失点。下端反投影到最大水平面（y 约 -1.467）。再要求上端射线离该脚的水平误差小于 0.35 米、高度至少 1.2 米、距相机不超过 4 米。离线，不进 60 帧回调，不改保存的平面。

结果：未收紧时三张照片共 215 个脚，俯视散开。收紧后全序列 37 个脚，贴着已有竖面，不成玻璃墙。见 `docs/evolution/figures/2026-10-15-cafe-floor-feet.png` 与 `docs/evolution/figures/2026-10-15-cafe-fusion-tall-feet.png`。这条假设停止，不再调门槛。

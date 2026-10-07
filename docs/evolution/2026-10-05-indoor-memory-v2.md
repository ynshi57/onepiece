# 室内物品记忆第二版：实现记录与未完成边界

## 乔布斯方向

用户要记住物品，而非无名坐标；退出后保留，重看时知道是否变化，解除水平桌面门槛。目标是点选物品→保存→恢复查看→变化证据，不能将“未分割到”解释为消失。此文是实施进度，不是产品验收完成证明。

## 专家独立 review 与裁决

- 罗根有条件同意：原子持久化、损坏与版本错误可见；地图加载不等于定位恢复，必须跟踪稳定+anchor一致后投影，有超时和照片路径。
- 思余有条件同意：名字可选、真实轮廓、修复18px圆点挤压标签；定位诊断归入反馈；人工与自动判断明确区分。
- 全麦/小马有条件同意：Apple Vision foreground实例仅是显著物体分割，不是任意物品身份；特征距离必须有校准数据，遮挡和视角未知不能下结论。
- 乔布斯采纳：实现可运行的数据、空间与对照链路；自动“仍在/原处未见”不以未经校准阈值发布。人工确认属于补充观察工具，不能算自动识别已完成。
- 三位协作者写入模块后遇到额度限制；主线程继续集成、检查与修复，并非独立review过的最终集成版本。

## 任务卡与接口

| 主责 | 范围与交付 | 验收 |
|---|---|---|
| 罗根 | IndoorMemoryStore actor，版本化binary plist、校验、原子替换、保护和排除备份；Controller恢复与串行保存 | fresh/损坏/重复ID/删除/重建controller；真机文件保护待验 |
| 全麦/小马 | IndoorTargetVision：正向图+左上归一化tap→mask/bounds/feature；真实距离+保守评估 | 未校准、遮挡/视角未知、无轮廓均不可宣称消失；真实分割准确度待验 |
| 思余 | Screen：标签、点照片、可选名、轮廓、对照、人工确认 | UI截图、无名保存/删除流程；实际mask叠层待真样例 |
| 主线程/乔布斯 | Controller集成、Model metadata、CURRENT和roadmap | 编译、相关测试、看图、明确剩余能力 |

Controller→Store：记录DTO+照片+JSON metadata+可选secure ARWorldMap；Store不授予空间有效性。
Controller→Vision：独立串行队列，输入正向缩小图，返回mask与特征；结果返回需校验记录/session/请求代次，防止切换后旧结果覆盖。
Controller→UI：Draft/保存状态/空间来源/比较状态/确认时间；UI不得从颜色或比较失败推断消失。

## 实现行为

1. 相机有新帧即可记录照片，不再要求水平面或强制名字。点选冻结照片中的目标，Vision提取轮廓；失败保留原因并允许照片保存。
2. 用记录帧的相机内参与方向把点映射到传感器射线。优先高置信sceneDepth，否则观测到的水平/垂直平面。无命中不创建锚点，禁止估计固定距离；平面位置明确可能在物品后方。
3. 照片首先落盘，然后尝试最多5秒地图保存；只有mapped采集新地图，地图包含当前记录对应anchors。重试保存入口在菜单。
4. 重开加载记录和可选地图。地图坏了照片仍保留，整份文件坏了阻止静默覆盖；明确清除才允许替换。恢复跟踪正常至少0.5秒并有对应anchor才投影，15秒失败明示并开始新扫描。
5. 历史mask展示为原视角轮廓缩略图，不是与当前视角精确贴合的3D轮廓。对照取当前照片，运行外观比较；目前没有经过评测的校准和遮挡/对齐证据，自动结果只能无法确认。
6. 用户可对当次照片人工确认仍在/原处未见，记录观察/确认时间和最近一次对照图。没有当前照片不能确认。查看旧记录时标明历史人工确认，不当作当前状态。
7. 本机受保护文件、不上传、不写相册、排除备份。模拟器不实现iOS文件保护，真机属性检查保留，不把模拟器结果当加密验证。

## 变更影响面

改动室内Model/Controller/Screen、新Store/Vision、三组单测与UI测试，CURRENT与roadmap同步。共享schema仅室内本地存储（版本1，旧原型没有磁盘资料需要迁移）。户外推流协议、后端、relay、模型资源和安装依赖未改变；使用系统ARKit/Vision，无新增下载模型/第三方依赖，setup_mac无需新增安装步骤。

## 验证记录

- 初次普通沙箱无法访问模拟器；授权构建成功。
- 第一次UI runner启动被模拟器拒绝；指定已启动设备、关闭并行并使用默认签名后可运行，不宣称原因已经完全证明。
- 首轮22单测+3UI暴露：fresh store文件缺失错误码处理遗漏、模拟器无保护属性、标签a11y子节点未暴露。分别修复缺失错误分类、区分真机保护检查、显式容器a11y与标签宽度；未删失败样本或放宽定位标准。
- 最新产品代码编译及22项单测通过：`/private/tmp/vqasee-next-regression.xcresult`。其中UI曾因把文字墨迹边界误当220pt布局容器而失败；实际四字文字宽约68pt、单行20pt，改为验证横向单行宽高比与高度，仍能捕获原先塌缩缺陷，未修改产品门槛。
- 同一产品代码最终3项UI通过（只修了上述测试断言）：`/private/tmp/vqasee-next-ui-final.xcresult`；覆盖标签、命名保存/删除、空名保存、室内/户外往返。`git diff --check`通过。
- 已亲自查看实际模拟器截图：标签名称/时间/距离可读，照片点选页布局可读，空名保存可用。截图使用明确标注的合成fixture，没有证明真实分割或AR准确度。截图目录：`/private/tmp/vqasee-next-regression-shots/`。
- 真机文件保护断言保留在非模拟器目标中，本轮未运行；真实mask叠层、地图重定位、深度/平面位置、可见延迟和自动变化识别均未真机验收。

## 真机与模型阻塞（不能宣布闭环）

本机模拟器不提供真实AR相机、深度和现场物品数据。尚无同一物品/相似物品/移走/遮挡/光照/视角变化的带真值照片序列；自动变化与身份阈值不能校准，不能靠合成policy测试称准确。需要实际iPhone在家/书店/咖啡店记录：不动、移走、遮挡、替换相似品、换视角，所有失败计入；同步测位置误差、恢复成功率和真实可见延迟。

普通无深度手机不规则表面精确3D定位仍未实现（照片可记，不代表空间无限制）；多视角三角化尚需鲁棒跟踪和误差证据。搜索移动后的新位置、完整三维轮廓与形变超出本轮。

## 一手依据

- [Apple 前景实例分割](https://developer.apple.com/documentation/vision/vngenerateforegroundinstancemaskrequest)
- [Apple 生成原图尺寸mask](https://developer.apple.com/documentation/vision/vninstancemaskobservation/generatescaledmaskforimage(forinstances:from:))
- [Apple 世界地图质量](https://developer.apple.com/documentation/arkit/arframe/worldmappingstatus-swift.property)
- [Apple mapped 条件与恢复限制](https://developer.apple.com/documentation/arkit/arframe/worldmappingstatus-swift.enum/mapped)

本轮经验沉淀到代码/测试、本文件、CURRENT及roadmap；没有修改AGENTS或skills长期规则。

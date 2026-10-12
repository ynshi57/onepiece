# 空间扫描：含定位地图的保存闪退

用户现场反馈：新版本点保存反复闪退。USB读取同一手机两份崩溃记录，最新记录主线程为SIGABRT，Swift AccessSet::insert / swift_beginAccess，调用点为SpatialScanController.save闭包。不是jetsam或看门狗超时。

根因：`snapshot.worldMap?.sha256 = try await store.saveWorldMap(mapData, scanID: snapshot.id)`。可变可选链首先取得snapshot的独占修改访问；右侧异步参数又读取snapshot.id，引发Swift运行时排他访问冲突。先独立读取id、等待校验码返回，再修改snapshot，消除重叠。

乔布斯：保存失败阻断客户旅程，优先修复，再推进ABC。罗根视角：依据真实栈修改内存访问，不按猜测改阈值或删地图。思余视角：保存与导出都要走完，不能只显示“已保存”。全麦视角：此问题与识别、分割、重建模型无关。小马技术审查：先明确语言运行时边界，避免以大重构替代定位。

修改SpatialScanController的初始化支持注入真实磁盘store；归档暂存步骤独立命名。SpatialScanTests新增Controller实际异步保存、地图写入、JSON重读、ZIP导出和重复保存回归；归档测试字节是存储协议fixture，不代表AR定位精度。旧测试只检查存储校验，未覆盖Controller的异步赋值，确实遗漏。

验证：iPhone17模拟器SpatialScanTests通过，含新回归；修复版已USB安装并成功启动同一iPhone。用户复测反馈“已保存”。USB进一步读取真实109MB导出包：239张照片、1798轨迹、25面和约25MB定位地图；全部manifest附件SHA与地图SHA通过，并已导入Mac、浏览器打开照片。保存/导出路径已真实验收；跨次重定位另行验证。

影响面：地图暂存→Controller保存→Store附件/记录→历史/导出；字段协议无改动，户外相机模型未修改。改动不删除地图或照片，不静默降级保存能力。经验：含await的复杂可变赋值需按读、等待、写分步；关键客户路径测试必须覆盖控制器而不仅是底层存储。

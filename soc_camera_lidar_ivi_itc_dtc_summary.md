# SOC 侧 Camera / Lidar / IVI 硬件故障 ITC-DTC 梳理

来源文件：
- `ICU1.5_ITC_Diag_20230630(1).xlsx`：主要看 `ITC_SOC`，这里才展开了 bit 级 ITC。
- `IDC3.0_Diagnostic_Requirement_V20231106.xlsx`：主要看 `DTC-List` / `DtcInfoExtractForAsw/Bsw`，这里多数是 DTC 级定义，部分行写“ITC表中做逻辑或”，但本文件没有展开对应 ITC bit。

## 1. 统计结论

| 对象 | ICU1.5 中明确 bit 级 ITC | ICU1.5 中占位/未展开 bit | IDC3.0 中硬件类 DTC | 结论 |
|---|---:|---:|---:|---|
| Camera | 92 | 36 | 26 | Camera 是两份文档中唯一有较多 SOC 侧硬件 ITC 展开的对象 |
| Lidar | 0 | 0 | 4 | IDC3.0 有 Lidar 硬件类 DTC，但未展开 ITC |
| IVI/HUT | 0 | 0 | 0 | 有 HUT/IVI 相关通信/信号 DTC，但不是 SOC 侧硬件 ITC |

> Camera 的 92 条明确 ITC = 5 个摄像头内部故障 DTC × 18 bit + AVM 串行器 2 bit。若把侧视摄像头模组/链路的空 bit 占位也算入，则 Camera 共 128 条 ITC 行。

## 2. ICU1.5：Camera 明确展开的 SOC 侧硬件 ITC

### 2.1 摄像头内部故障：5 个 DTC，每个 18 条 ITC

| DTC | DTC 名称 | bit | ITC / 故障细节 | 失败条件摘要 | 通过条件摘要 |
|---|---|---:|---|---|---|
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit0 | CAM_DES_CHANNEL_1_WARN_INT_ERR |  |  |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit2 | CAM_SENSOR_CHANNEL_08_15_4_WARN_INT_ERR |  |  |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit3 | CAM_SER_CHANNEL_00_07_4_WARN_DEC_ERR_FLAG_A | Decoding error flag for Link A, asserted, <br>when DEC_ERR_A ≥ DEC_ERR_THR | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit4 | CAM_SER_CHANNEL_00_07_4_WARN_IDLE_ERR_FLAG | Idle word error flag, asserted when, <br>IDLE_ERR ≥ DEC_ERR_THR | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit5 | CAM_SER_CHANNEL_00_07_4_WARN_PHY_INT_A | PHY Interrupt of Link A | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit6 | CAM_SER_CHANNEL_00_07_4_WARN_PKT_CNT_FLAG | Packet count flag, asserted when, <br>PKT_CNT ≥ PKT_CNT_THR | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit7 | CAM_SER_CHANNEL_00_07_4_WARN_RT_CNT_FLAG | Combined ARQ re-transmission event flag, asserted when any of the selected, <br>channels have done at least one ARQ retransmission | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit8 | CAM_SER_CHANNEL_00_07_4_WARN_MAX_RT_FLAG | Combined ARQ maximum re-transmission, <br>limit error flag, asserted when any of the, <br>selected channels ARQ re-transmission, <br>limit is reached | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit9 | CAM_SER_CHANNEL_00_07_4_WARN_VDD18_OV_FLAG | VDD18 over-voltage indication. This bit is, <br>sticky. It is set when VDD18 is over the, <br>over-voltage threshold. It is cleared when, <br>read | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit10 | CAM_SER_CHANNEL_00_07_4_WARN_VDD_OV_FLAG | VDD over-voltage indication. This bit is, <br>sticky. It is set when VDD is over the overvoltage, <br>threshold. It is cleared when read. | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit11 | CAM_SER_CHANNEL_08_15_4_WARN_EOM_ERR_FLAG_A | Eye Opening is below configured threshold, <br>for Link A | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit12 | CAM_SER_CHANNEL_08_15_4_WARN_VREG_OV_FLAG | VREG over-voltage indication. This bit is, <br>sticky. It is set when VREG is over the, <br>over-voltage threshold. It is cleared when, <br>read. | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit13 | CAM_SER_CHANNEL_08_15_4_WARN_MIPI_ERR_FLAG | MIPI RX error flag, asserted when any of these is asserted: phy0_hs_err [0], [1], [4], [5] phy1_hs_err [0], [1], [4], [5], <br>phy2_hs_err [0], [1], [4], [5] phy3_hs_err [0], [1], [4], [5], <br>ctrl0_csi_err_l [0], [1], [7] ctrl0_csi_err_h [0], <br>ctrl1_csi_err_l [0], [1], [7], <br>ctrl1_csi_err_h [0] ctrl0_dsi_err_l [0], [1], [2], [3], [4], [5], [6], <br>ctrl1_dsi_err_l [0], [1], [2], [3], [4], [5], [6] | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit14 | CAM_SER_CHANNEL_08_15_4_WARN_VPRBS_ERR_FLAG | Video PRBS error flag, asserted when VPRBS_ERR > 0 | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit15 | CAM_SER_CHANNEL_08_15_4_WARN_RTTN_CRC_INT | Retention memory restore CRC error interrupt. When the device wakes up, contents of retention memory is loaded back to main registers. The restored data is covered by CRC. If CRC fails this bit is set. | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit16 | CAM_SER_CHANNEL_08_15_4_WARN_EFUSE_CRC_ERR | An error ocurred on the efuse CRC calculation. | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit17 | CAM_SER_CHANNEL_08_15_4_WARN_VDDBAD_INT_FLAG | Combined VDD bad indicator. Asserts when either VDDBAD_STATUS[1] or [0] are 1. | 标记位清0, ERRB中断信号置1 |
| C1C3049 | 前行车记录仪摄像头内部故障<br>FrontWideCamera internal error | bit18 | CAM_SER_CHANNEL_08_15_4_WARN_PORZ_INT_FLAG | PORZ interrupt flag. Asserts when either PORZ_STATUS[5] or [4] are 0. | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit0 | CAM_DES_CHANNEL_0_WARN_INT_ERR |  |  |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit1 | CAM_SENSOR_CHANNEL_08_15_0_WARN_INT_ERR |  |  |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit2 | CAM_SER_CHANNEL_00_07_0_WARN_DEC_ERR_FLAG_A | Decoding error flag for Link A, asserted, <br>when DEC_ERR_A ≥ DEC_ERR_THR | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit3 | CAM_SER_CHANNEL_00_07_0_WARN_IDLE_ERR_FLAG | Idle word error flag, asserted when, <br>IDLE_ERR ≥ DEC_ERR_THR | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit4 | CAM_SER_CHANNEL_00_07_0_WARN_PHY_INT_A | PHY Interrupt of Link A | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit5 | CAM_SER_CHANNEL_00_07_0_WARN_PKT_CNT_FLAG | Packet count flag, asserted when, <br>PKT_CNT ≥ PKT_CNT_THR | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit6 | CAM_SER_CHANNEL_00_07_0_WARN_RT_CNT_FLAG | Combined ARQ re-transmission event flag, asserted when any of the selected, <br>channels have done at least one ARQ retransmission | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit7 | CAM_SER_CHANNEL_00_07_0_WARN_MAX_RT_FLAG | Combined ARQ maximum re-transmission, <br>limit error flag, asserted when any of the, <br>selected channels ARQ re-transmission, <br>limit is reached | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit8 | CAM_SER_CHANNEL_00_07_0_WARN_VDD18_OV_FLAG | VDD18 over-voltage indication. This bit is, <br>sticky. It is set when VDD18 is over the, <br>over-voltage threshold. It is cleared when, <br>read | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit9 | CAM_SER_CHANNEL_00_07_0_WARN_VDD_OV_FLAG | VDD over-voltage indication. This bit is, <br>sticky. It is set when VDD is over the overvoltage, <br>threshold. It is cleared when read. | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit10 | CAM_SER_CHANNEL_08_15_0_WARN_EOM_ERR_FLAG_A | Eye Opening is below configured threshold, <br>for Link A | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit11 | CAM_SER_CHANNEL_08_15_0_WARN_VREG_OV_FLAG | VREG over-voltage indication. This bit is, <br>sticky. It is set when VREG is over the, <br>over-voltage threshold. It is cleared when, <br>read. | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit12 | CAM_SER_CHANNEL_08_15_0_WARN_MIPI_ERR_FLAG | MIPI RX error flag, asserted when any of these is asserted: phy0_hs_err [0], [1], [4], [5] phy1_hs_err [0], [1], [4], [5], <br>phy2_hs_err [0], [1], [4], [5] phy3_hs_err [0], [1], [4], [5], <br>ctrl0_csi_err_l [0], [1], [7] ctrl0_csi_err_h [0], <br>ctrl1_csi_err_l [0], [1], [7], <br>ctrl1_csi_err_h [0] ctrl0_dsi_err_l [0], [1], [2], [3], [4], [5], [6], <br>ctrl1_dsi_err_l [0], [1], [2], [3], [4], [5], [6] | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit13 | CAM_SER_CHANNEL_08_15_0_WARN_VPRBS_ERR_FLAG | Video PRBS error flag, asserted when VPRBS_ERR > 0 | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit14 | CAM_SER_CHANNEL_08_15_0_WARN_RTTN_CRC_INT | Retention memory restore CRC error interrupt. When the device wakes up, contents of retention memory is loaded back to main registers. The restored data is covered by CRC. If CRC fails this bit is set. | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit15 | CAM_SER_CHANNEL_08_15_0_WARN_EFUSE_CRC_ERR | An error ocurred on the efuse CRC calculation. | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit16 | CAM_SER_CHANNEL_08_15_0_WARN_VDDBAD_INT_FLAG | Combined VDD bad indicator. Asserts when either VDDBAD_STATUS[1] or [0] are 1. | 标记位清0, ERRB中断信号置1 |
| C1C3149 | 前环视摄像头内部故障<br>FrontShortCamera internal error | bit17 | CAM_SER_CHANNEL_08_15_0_WARN_PORZ_INT_FLAG | PORZ interrupt flag. Asserts when either PORZ_STATUS[5] or [4] are 0. | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit0 | CAM_DES_CHANNEL_0_WARN_INT_ERR |  |  |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit1 | CAM_SENSOR_CHANNEL_08_15_3_WARN_INT_ERR |  |  |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit2 | CAM_SER_CHANNEL_00_07_3_WARN_DEC_ERR_FLAG_A | Decoding error flag for Link A, asserted, <br>when DEC_ERR_A ≥ DEC_ERR_THR | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit3 | CAM_SER_CHANNEL_00_07_3_WARN_IDLE_ERR_FLAG | Idle word error flag, asserted when, <br>IDLE_ERR ≥ DEC_ERR_THR | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit4 | CAM_SER_CHANNEL_00_07_3_WARN_PHY_INT_A | PHY Interrupt of Link A | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit5 | CAM_SER_CHANNEL_00_07_3_WARN_PKT_CNT_FLAG | Packet count flag, asserted when, <br>PKT_CNT ≥ PKT_CNT_THR | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit6 | CAM_SER_CHANNEL_00_07_3_WARN_RT_CNT_FLAG | Combined ARQ re-transmission event flag, asserted when any of the selected, <br>channels have done at least one ARQ retransmission | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit7 | CAM_SER_CHANNEL_00_07_3_WARN_MAX_RT_FLAG | Combined ARQ maximum re-transmission, <br>limit error flag, asserted when any of the, <br>selected channels ARQ re-transmission, <br>limit is reached | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit8 | CAM_SER_CHANNEL_00_07_3_WARN_VDD18_OV_FLAG | VDD18 over-voltage indication. This bit is, <br>sticky. It is set when VDD18 is over the, <br>over-voltage threshold. It is cleared when, <br>read | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit9 | CAM_SER_CHANNEL_00_07_3_WARN_VDD_OV_FLAG | VDD over-voltage indication. This bit is, <br>sticky. It is set when VDD is over the overvoltage, <br>threshold. It is cleared when read. | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit10 | CAM_SER_CHANNEL_08_15_3_WARN_EOM_ERR_FLAG_A | Eye Opening is below configured threshold, <br>for Link A | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit11 | CAM_SER_CHANNEL_08_15_3_WARN_VREG_OV_FLAG | VREG over-voltage indication. This bit is, <br>sticky. It is set when VREG is over the, <br>over-voltage threshold. It is cleared when, <br>read. | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit12 | CAM_SER_CHANNEL_08_15_3_WARN_MIPI_ERR_FLAG | MIPI RX error flag, asserted when any of these is asserted: phy0_hs_err [0], [1], [4], [5] phy1_hs_err [0], [1], [4], [5], <br>phy2_hs_err [0], [1], [4], [5] phy3_hs_err [0], [1], [4], [5], <br>ctrl0_csi_err_l [0], [1], [7] ctrl0_csi_err_h [0], <br>ctrl1_csi_err_l [0], [1], [7], <br>ctrl1_csi_err_h [0] ctrl0_dsi_err_l [0], [1], [2], [3], [4], [5], [6], <br>ctrl1_dsi_err_l [0], [1], [2], [3], [4], [5], [6] | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit13 | CAM_SER_CHANNEL_08_15_3_WARN_VPRBS_ERR_FLAG | Video PRBS error flag, asserted when VPRBS_ERR > 0 | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit14 | CAM_SER_CHANNEL_08_15_3_WARN_RTTN_CRC_INT | Retention memory restore CRC error interrupt. When the device wakes up, contents of retention memory is loaded back to main registers. The restored data is covered by CRC. If CRC fails this bit is set. | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit15 | CAM_SER_CHANNEL_08_15_3_WARN_EFUSE_CRC_ERR | An error ocurred on the efuse CRC calculation. | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit16 | CAM_SER_CHANNEL_08_15_3_WARN_VDDBAD_INT_FLAG | Combined VDD bad indicator. Asserts when either VDDBAD_STATUS[1] or [0] are 1. | 标记位清0, ERRB中断信号置1 |
| C1C3249 | 左环视摄像头内部故障<br>LeftShortCamera internal error | bit17 | CAM_SER_CHANNEL_08_15_3_WARN_PORZ_INT_FLAG | PORZ interrupt flag. Asserts when either PORZ_STATUS[5] or [4] are 0. | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit0 | CAM_DES_CHANNEL_0_WARN_INT_ERR |  |  |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit1 | CAM_SENSOR_CHANNEL_08_15_2_WARN_INT_ERR |  |  |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit2 | CAM_SER_CHANNEL_00_07_2_WARN_DEC_ERR_FLAG_A | Decoding error flag for Link A, asserted, <br>when DEC_ERR_A ≥ DEC_ERR_THR | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit3 | CAM_SER_CHANNEL_00_07_2_WARN_IDLE_ERR_FLAG | Idle word error flag, asserted when, <br>IDLE_ERR ≥ DEC_ERR_THR | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit4 | CAM_SER_CHANNEL_00_07_2_WARN_PHY_INT_A | PHY Interrupt of Link A | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit5 | CAM_SER_CHANNEL_00_07_2_WARN_PKT_CNT_FLAG | Packet count flag, asserted when, <br>PKT_CNT ≥ PKT_CNT_THR | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit6 | CAM_SER_CHANNEL_00_07_2_WARN_RT_CNT_FLAG | Combined ARQ re-transmission event flag, asserted when any of the selected, <br>channels have done at least one ARQ retransmission | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit7 | CAM_SER_CHANNEL_00_07_2_WARN_MAX_RT_FLAG | Combined ARQ maximum re-transmission, <br>limit error flag, asserted when any of the, <br>selected channels ARQ re-transmission, <br>limit is reached | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit8 | CAM_SER_CHANNEL_00_07_2_WARN_VDD18_OV_FLAG | VDD18 over-voltage indication. This bit is, <br>sticky. It is set when VDD18 is over the, <br>over-voltage threshold. It is cleared when, <br>read | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit9 | CAM_SER_CHANNEL_00_07_2_WARN_VDD_OV_FLAG | VDD over-voltage indication. This bit is, <br>sticky. It is set when VDD is over the overvoltage, <br>threshold. It is cleared when read. | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit10 | CAM_SER_CHANNEL_08_15_2_WARN_EOM_ERR_FLAG_A | Eye Opening is below configured threshold, <br>for Link A | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit11 | CAM_SER_CHANNEL_08_15_2_WARN_VREG_OV_FLAG | VREG over-voltage indication. This bit is, <br>sticky. It is set when VREG is over the, <br>over-voltage threshold. It is cleared when, <br>read. | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit12 | CAM_SER_CHANNEL_08_15_2_WARN_MIPI_ERR_FLAG | MIPI RX error flag, asserted when any of these is asserted: phy0_hs_err [0], [1], [4], [5] phy1_hs_err [0], [1], [4], [5], <br>phy2_hs_err [0], [1], [4], [5] phy3_hs_err [0], [1], [4], [5], <br>ctrl0_csi_err_l [0], [1], [7] ctrl0_csi_err_h [0], <br>ctrl1_csi_err_l [0], [1], [7], <br>ctrl1_csi_err_h [0] ctrl0_dsi_err_l [0], [1], [2], [3], [4], [5], [6], <br>ctrl1_dsi_err_l [0], [1], [2], [3], [4], [5], [6] | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit13 | CAM_SER_CHANNEL_08_15_2_WARN_VPRBS_ERR_FLAG | Video PRBS error flag, asserted when VPRBS_ERR > 0 | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit14 | CAM_SER_CHANNEL_08_15_2_WARN_RTTN_CRC_INT | Retention memory restore CRC error interrupt. When the device wakes up, contents of retention memory is loaded back to main registers. The restored data is covered by CRC. If CRC fails this bit is set. | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit15 | CAM_SER_CHANNEL_08_15_2_WARN_EFUSE_CRC_ERR | An error ocurred on the efuse CRC calculation. | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit16 | CAM_SER_CHANNEL_08_15_2_WARN_VDDBAD_INT_FLAG | Combined VDD bad indicator. Asserts when either VDDBAD_STATUS[1] or [0] are 1. | 标记位清0, ERRB中断信号置1 |
| C1C3349 | 右环视摄像头内部故障<br>RightShortCamera internal error | bit17 | CAM_SER_CHANNEL_08_15_2_WARN_PORZ_INT_FLAG | PORZ interrupt flag. Asserts when either PORZ_STATUS[5] or [4] are 0. | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit0 | CAM_DES_CHANNEL_0_WARN_INT_ERR |  |  |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit1 | CAM_SENSOR_CHANNEL_08_15_1_WARN_INT_ERR |  |  |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit2 | CAM_SER_CHANNEL_00_07_1_WARN_DEC_ERR_FLAG_A | Decoding error flag for Link A, asserted, <br>when DEC_ERR_A ≥ DEC_ERR_THR | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit3 | CAM_SER_CHANNEL_00_07_1_WARN_IDLE_ERR_FLAG | Idle word error flag, asserted when, <br>IDLE_ERR ≥ DEC_ERR_THR | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit4 | CAM_SER_CHANNEL_00_07_1_WARN_PHY_INT_A | PHY Interrupt of Link A | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit5 | CAM_SER_CHANNEL_00_07_1_WARN_PKT_CNT_FLAG | Packet count flag, asserted when, <br>PKT_CNT ≥ PKT_CNT_THR | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit6 | CAM_SER_CHANNEL_00_07_1_WARN_RT_CNT_FLAG | Combined ARQ re-transmission event flag, asserted when any of the selected, <br>channels have done at least one ARQ retransmission | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit7 | CAM_SER_CHANNEL_00_07_1_WARN_MAX_RT_FLAG | Combined ARQ maximum re-transmission, <br>limit error flag, asserted when any of the, <br>selected channels ARQ re-transmission, <br>limit is reached | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit8 | CAM_SER_CHANNEL_00_07_1_WARN_VDD18_OV_FLAG | VDD18 over-voltage indication. This bit is, <br>sticky. It is set when VDD18 is over the, <br>over-voltage threshold. It is cleared when, <br>read | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit9 | CAM_SER_CHANNEL_00_07_1_WARN_VDD_OV_FLAG | VDD over-voltage indication. This bit is, <br>sticky. It is set when VDD is over the overvoltage, <br>threshold. It is cleared when read. | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit10 | CAM_SER_CHANNEL_08_15_1_WARN_EOM_ERR_FLAG_A | Eye Opening is below configured threshold, <br>for Link A | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit11 | CAM_SER_CHANNEL_08_15_1_WARN_VREG_OV_FLAG | VREG over-voltage indication. This bit is, <br>sticky. It is set when VREG is over the, <br>over-voltage threshold. It is cleared when, <br>read. | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit12 | CAM_SER_CHANNEL_08_15_1_WARN_MIPI_ERR_FLAG | MIPI RX error flag, asserted when any of these is asserted: phy0_hs_err [0], [1], [4], [5] phy1_hs_err [0], [1], [4], [5], <br>phy2_hs_err [0], [1], [4], [5] phy3_hs_err [0], [1], [4], [5], <br>ctrl0_csi_err_l [0], [1], [7] ctrl0_csi_err_h [0], <br>ctrl1_csi_err_l [0], [1], [7], <br>ctrl1_csi_err_h [0] ctrl0_dsi_err_l [0], [1], [2], [3], [4], [5], [6], <br>ctrl1_dsi_err_l [0], [1], [2], [3], [4], [5], [6] | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit13 | CAM_SER_CHANNEL_08_15_1_WARN_VPRBS_ERR_FLAG | Video PRBS error flag, asserted when VPRBS_ERR > 0 | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit14 | CAM_SER_CHANNEL_08_15_1_WARN_RTTN_CRC_INT | Retention memory restore CRC error interrupt. When the device wakes up, contents of retention memory is loaded back to main registers. The restored data is covered by CRC. If CRC fails this bit is set. | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit15 | CAM_SER_CHANNEL_08_15_1_WARN_EFUSE_CRC_ERR | An error ocurred on the efuse CRC calculation. | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit16 | CAM_SER_CHANNEL_08_15_1_WARN_VDDBAD_INT_FLAG | Combined VDD bad indicator. Asserts when either VDDBAD_STATUS[1] or [0] are 1. | 标记位清0, ERRB中断信号置1 |
| C1C3449 | 后环视摄像头内部故障<br>RearShortCamera internal error | bit17 | CAM_SER_CHANNEL_08_15_1_WARN_PORZ_INT_FLAG | PORZ interrupt flag. Asserts when either PORZ_STATUS[5] or [4] are 0. | 标记位清0, ERRB中断信号置1 |

### 2.2 AVM 串行器故障：2 条明确 ITC

| DTC | DTC 名称 | bit | ITC / 故障细节 | 监控信号 |
|---|---|---:|---|---|
| 5C2C49 | 美信芯片AVM串行器故障 | bit0 | CAM_DES_CHANNEL_0_SAFE_AVM_SER_ERR | SER1_LOCK_ERRB_VS |
| 5C2C49 | 美信芯片AVM串行器故障 | bit1 | CAM_DES_CHANNEL_1_SAFE_AVM_SER_ERR |  |

### 2.3 侧视摄像头模组/链路：36 条 bit 行，但细节未展开

这些行在 `ITC_SOC` 中有 DTC 和 bit，但故障细节/监控信号/失败条件大多为空，因此建议按“占位或待供应商/软件补充”的 ITC 行处理。

| DTC | DTC 名称 | bit 数 | 说明 |
|---|---|---:|---|
| U110104 | 左前侧视摄像头模组故障 | 5 | bit 行存在，但故障细节未展开 |
| U110204 | 右前侧视摄像头模组故障 | 5 | bit 行存在，但故障细节未展开 |
| U110304 | 右后侧视摄像头模组故障 | 5 | bit 行存在，但故障细节未展开 |
| U110404 | 左后侧视摄像头模组故障 | 5 | bit 行存在，但故障细节未展开 |
| U110504 | 侧视链路硬件故障 | 16 | bit 行存在，但故障细节未展开 |

## 3. IDC3.0：Camera 硬件类 DTC（DTC 级，不展开 ITC）

IDC3.0 中 Camera 硬件类 DTC 共 26 个。多数监控配置写的是“ITC表中做逻辑或，具体关联信号见ITC表”，但当前 IDC3.0 文件没有给出这些 ITC 的 bit 级列表。

| DTC No | 故障码 | 名称 | 中文名 | 类型 | 监控配置/监控报文 | 失败条件 | 通过条件 |
|---|---|---|---|---|---|---|---|
| D10104 | U110104 | FrontLeft SideCamera Moudle Failure | 左前侧视摄像头模组故障 | Side Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 1 times continously |
| D10204 | U110204 | FrontRight SideCamera Moudle Failure | 右前侧视摄像头模组故障 | Side Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 1 times continously |
| D10304 | U110304 | RearRight SideCamera Moudle Failure | 右后侧视摄像头模组故障 | Side Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 1 times continously |
| D10404 | U110404 | RearLeft SideCamera Moudle Failure | 左后侧视摄像头模组故障 | Side Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 1 times continously |
| D10504 | U110504 | SideCamera Link Error | 侧视摄像头链路故障 | Side Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 1 times continously |
| D10904 | U110904 | SideCamera Disconnect ERR | 侧视摄像头与控制器连接中断故障 | Side Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 2 times continously |
| D10604 | U110604 | Long Distance Front View Camera Module ERR | FVC1前视长距镜头模组的故障 | FV | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 4 times continously |
| D10704 | U110704 | Middle Distance Front View Camera Module ERR | FVC2前视中距镜头模组的故障 | FV | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 4 times continously |
| D10804 | U110804 | Short Distance Front View Camera Module ERR | FVC3前视短距镜头模组的故障 | FV | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 4 times continously |
| D10A04 | U110A04 | Middle Distance Rear View Camera Module ERR | FVC4后视中距镜头模组的故障 | FV | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 4 times continously |
| D10B04 | U110B04 | Long Distance Front View Camera Link ERR | FVC1前视长距视觉链路故障 | FV | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 4 times continously |
| D10C04 | U110C04 | Middle Distance Front View Camera Link ERR | FVC2前视中距视觉链路故障 | FV | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 4 times continously |
| D10D04 | U110D04 | Short Distance Front View Camera Link ERR | FVC3前视短距视觉链路故障 | FV | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 4 times continously |
| D10E04 | U110E04 | Middle Distance Rear View Camera Link ERR | FVC4后视中距视觉链路故障 | FV | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 4 times continously |
| D10F04 | U110F04 | 8M Vision Link ERR | 8M视觉链路故障 | FV | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 4 times continously |
| D11A04 | U111A04 | Long Distance Front View Camera Disconnect ERR | FVC1前视长距镜头与控制器连接中断故障 | FV | Gw5FailCode_e=54<br>Gw5FailCode_e=55 | Signal invalid or error for 4 times continously | Received correct signal for 4 times continously |
| D11B04 | U111B04 | Middle Distance Front View Camera Disconnect ERR | FVC2前视中距镜头与控制器连接中断故障 | FV | Gw5FailCode_e=54<br>Gw5FailCode_e=55 | Signal invalid or error for 4 times continously | Received correct signal for 4 times continously |
| D11C04 | U111C04 | Short Distance Front View Camera Disconnect ERR | FVC3前视短距镜头与控制器连接中断故障 | FV | Gw5FailCode_e=54<br>Gw5FailCode_e=55 | Signal invalid or error for 4 times continously | Received correct signal for 4 times continously |
| D11D04 | U111D04 | Middle Distance Rear View Camera Disconnect ERR | FVC4后视中距镜头与控制器连接中断故障 | FV | Gw5FailCode_e=54<br>Gw5FailCode_e=55 | Signal invalid or error for 4 times continously | Received correct signal for 4 times continously |
| D11D01 | U111D01 | Vision Power ERR | 视觉电源故障 | FV | N\A | ITC表中做逻辑或，具体关联信号见ITC表 | Received correct signal for 1 times continously |
| D1285B | U11285B | Front AVMCamera Moudle Failure | 前环视摄像头模组故障 | AVP Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 1 times continously |
| D1295B | U11295B | Rear AVMCamera Moudle Failure | 后环视摄像头模组故障 | AVP Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 1 times continously |
| D12A5B | U112A5B | Right AVMCamera Moudle Failure | 右环视摄像头模组故障 | AVP Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 1 times continously |
| D12B5B | U112B5B | Left AVMCamera Moudle Failure | 左环视摄像头模组故障 | AVP Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 1 times continously |
| D1235B | U11235B | AVMCamera Link Error | 环视摄像头链路故障 | AVP Camera | ITC表中做逻辑或，具体关联信号见ITC表 |  | Received correct signal for 1 times continously |
| D12D5B | U112D5B | AVMCamera Disconnect ERR | 环视摄像头与控制器连接中断故障 | AVP Camera |  |  | Received correct signal for 1 times continously |

## 4. IDC3.0：Lidar 硬件类 DTC（DTC 级，不展开 ITC）

IDC3.0 中 Lidar 硬件类 DTC 共 4 个，均写有“ITC表中做逻辑或，具体关联信号见ITC表”，但未在当前文件展开 ITC bit。

| DTC No | 故障码 | 名称 | 中文名 | 类型 | 监控配置/监控报文 | 失败条件 | 通过条件 |
|---|---|---|---|---|---|---|---|
| D01104 | U101104 | Front Left Lidar Failure | 左前激光雷达故障 | Lidar | ITC表中做逻辑或，具体关联信号见ITC表 | Signal invalid or error for 3 times continously | Received correct signal for 3 times continously |
| D01204 | U101204 | Front Right Lidar Failure | 右前激光雷达故障 | Lidar | ITC表中做逻辑或，具体关联信号见ITC表 | Signal invalid or error for 3 times continously | Received correct signal for 3 times continously |
| D01304 | U101304 | Front Lidar Failure | 前激光雷达故障 | Lidar | ITC表中做逻辑或，具体关联信号见ITC表 | Signal invalid or error for 3 times continously | Received correct signal for 3 times continously |
| D01404 | U101404 | Rear Lidar Failure | 后激光雷达故障 | Lidar | ITC表中做逻辑或，具体关联信号见ITC表 | Signal invalid or error for 3 times continously | Received correct signal for 3 times continously |

## 5. IVI/HUT 结论

当前两份文档中没有发现“IVI/HUT 的 SOC 侧硬件 ITC”展开。IDC3.0 有 HUT 相关 DTC，但主要是信号无效、E2E、丢通讯、SecOC 等，不是 SOC 侧硬件 ITC。

| 故障码 | 名称 | 中文名 | 类型 | 说明 |
|---|---|---|---|---|
| U123681 | IFC_SnvtySet Signal Invalid Value | 预警辅助灵敏度信号异常 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |
| U123781 | LSSWarnFormSwtReq Signal Invalid Value | 预警方式选择开关异常 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |
| U123881 | FCW_SnvtySet Signal Invalid Value | 前碰撞预警灵敏度信号异常 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |
| U123981 | TSR_SnvtySet Signal Invalid Value | 超速报警灵敏度信号异常 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |
| U124181 | NavSpdLim Signal Group Invalid Value | 导航限速信号异常 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |
| U124381 | Instrument Panel Error Status | 多媒体主机仪表错误 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |
| U142981 | HUT Message Rolling Counter Error | 导航地图滚动计数器错误 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |
| U024687 | Lost Communication With Head Unit System 2 | 与导航主机系统失去通讯1 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |
| U024787 | Lost Communication With Head Unit System 3 | 与导航主机系统失去通讯2 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |
| U024587 | Lost Communication With Head Unit System 1 | 与导航主机系统失去通讯3 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |
| U129881 | PrkgCtrlModReqValid Error | HUT泊车控制模式请求无效 | HUT | HUT/IVI 相关，但不是 SOC 侧硬件 ITC |

## 6. 对 SOC 侧硬件同学的建议：供应商 SDK 原始故障是否应该做 DTC 诊断？

建议要做，但不要简单理解成“供应商 SDK 每一个原始故障都直接变成一个 DTC”。更合理的分层是：

```text
供应商 SDK 原始 fault / error / status
        ↓ 归一化、去抖、过滤、分级
SOC 内部 ITC bit / internal fault
        ↓ 多个 ITC 逻辑或/聚合
对外 DTC
        ↓
MCU 诊断管理、UDS 0x19 读取、快照/扩展数据、功能降级
```

推荐职责划分：

| 角色 | 建议职责 |
|---|---|
| SOC 硬件/驱动同学 | 从 Camera/Lidar/SerDes/PMIC/ISP/SDK 中拿到原始故障寄存器或 SDK error code，完成故障解释、去抖、恢复条件、严重度分级，并输出稳定的 ITC bit。 |
| SOC 软件/算法同学 | 使用 ITC 或抽象后的 fault state 做算法输入保护、功能降级、状态机退出。 |
| MCU/诊断同学 | 把 SOC 上报的 ITC/fault state 映射为 DTC，负责 DEM/UDS/存储/清除/快照/扩展数据。 |
| 系统/诊断架构 | 定义“一组 SDK 原始故障 → ITC → DTC”的映射规则，避免 DTC 数量爆炸，也避免根因不可追溯。 |

原则：

1. **SDK 原始故障必须留痕**：至少要能通过 ITC bit、快照、扩展数据或内部日志追到原始 error code。
2. **不是每个 SDK error 都建一个 DTC**：DTC 面向售后和整车诊断，应该按可维修件/功能影响/安全影响聚合。
3. **ITC 适合承接 SDK 细故障**：例如 `MIPI_ERR`、`PHY_INT`、`DEC_ERR`、`VDD_OV`、`EFUSE_CRC_ERR` 这类供应商原始状态，适合做 ITC。
4. **DTC 适合做对外归类**：例如“前环视摄像头模组故障”“Lidar Failure”“视觉电源故障”。
5. **必须定义恢复条件**：只定义 set 条件不够，还要定义 clear/pass 条件、去抖时间、是否 latch、是否可 0x14 清除。

因此，对于 SOC 侧 Camera/Lidar/IVI 等硬件，建议硬件/驱动同学把供应商 SDK 原始故障整理成一张映射表，至少包含：

| 字段 | 示例 |
|---|---|
| Supplier raw fault | `DEC_ERR_A >= DEC_ERR_THR` |
| Register / SDK API | `SER_WARN_DEC_ERR_FLAG_A` |
| SOC ITC bit | `C1C3049.bit3` |
| DTC 聚合 | `C1C3049 前行车记录仪摄像头内部故障` |
| Set 条件 | 连续 N 次异常 / 中断置位 / error code 非 0 |
| Pass 条件 | 标志位清 0 / SDK 状态恢复 / 连续 N 次正常 |
| 功能影响 | 记录 DTC / 抑制 AVP / 抑制 L3_TJP 等 |
| 是否上报 MCU | 是 |
| 快照/扩展数据 | 原始 error code、通道号、时间戳、温度/电压等 |

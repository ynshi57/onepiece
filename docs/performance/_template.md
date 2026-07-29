# 性能审查：标题

## 背景

- 场景：
- 模式：
- 用户感知问题：

## 链路分解

```text
iPhone camera → image encode → WebSocket/direct or relay → backend queue → model inference → schema parse → iOS state → speech gate → voice output
```

## 数据

- end-to-end：
- encode：
- network + queue：
- model：
- speech：
- timeout rate：
- reconnect success：

## 瓶颈判断

- 最可能瓶颈：
- 证据：
- 需要补充的日志：

## 改动方案

- 最小修复：
- 风险：
- 回滚方式：

## 验证

- 命令：
- 人工测试：
- 结果：

## 结论

- 是否达标：
- 下一步：

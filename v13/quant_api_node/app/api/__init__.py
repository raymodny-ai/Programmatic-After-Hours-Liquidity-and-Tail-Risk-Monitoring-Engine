"""V13 API 包。

路由命名空间：

  /api/health                  - 健康检查（无 v1 前缀，便于 LB 直接探测）
  /api/v1/options/skew         - 当前 Skew 截面
  /api/v1/options/surface      - 单标的期权链 3D 表面
  /api/v1/macro/leverage       - 宏观杠杆截面
  /api/v1/alerts/recent        - 最近告警
  /api/v1/config               - 风控配置读写 (YAML)
  /api/v1/audit                - 审计日志
  /ws/alerts                   - WebSocket 双向推送
"""

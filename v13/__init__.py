"""V1.3 microservice monorepo.

包结构：

- v13.quant_api_node   — FastAPI Headless 后端 (REST + WebSocket)
- v13.quant_state_node — Redis 热缓存 + SQLite 持久化
- v13.quant_ui_node    — Next.js + TypeScript 前端（静态导出 + SSR）
- v13.shared           — 跨服务契约 (Pydantic Schemas)
"""
__version__ = "1.3.0"

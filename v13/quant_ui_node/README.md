# V1.3 Frontend (quant-ui-node)

> Next.js 14 + TypeScript · TradingView Lightweight-Charts · Three.js · xterm.js

## 页面导航

| 路径 | 视图 | 功能 |
| --- | --- | --- |
| `/` | HUD 概览 | Skew 25d 矩阵卡片 + 实时告警流 + 状态灯 + 日期选择器 |
| `/view-a` | 视图 A | 宏观流动性比率（M2 / Margin Debt）+ Skew 25d TradingView 联动 |
| `/view-b` | 视图 B | Three.js 3D 波动率曲面（鼠标拖动旋转、滚轮缩放、IV 强度色彩） |
| `/view-c` | 视图 C | YAML 配置编辑器 + 阈值滑块（保存到 SQLite risk_config） |
| `/logs` | 终端 | xterm.js 实时滚动日志 + 历史审计 |

## 开发

```bash
# 1. 安装依赖（首次）
npm install

# 2. 启动开发服务器
NEXT_PUBLIC_API_BASE=http://localhost:8000 \
NEXT_PUBLIC_WS_BASE=ws://localhost:8000 \
npm run dev
```

访问 `http://localhost:3000`。

## 生产构建

```bash
npm run build
NODE_ENV=production npm start
```

## Docker

参见根目录 `docker-compose.yml`：`quant-ui-node` 服务使用 `Dockerfile.ui`
三阶段构建（deps / builder / runner），最终镜像基于 `next start`。

## 环境变量

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | FastAPI 后端地址（构建时注入） |
| `NEXT_PUBLIC_WS_BASE` | `ws://localhost:8000` | WebSocket 入口地址 |

通过 `next.config.js` 的 `rewrites()` 转发 `/api/*` 与 `/ws/*` 到后端，
无需在客户端写绝对地址。

## 技术栈

- **Next.js 14.2** （App Router）
- **React 18.3** + **TypeScript 5.4**
- **Tailwind CSS 3.4** （自定义 dark 配色）
- **lightweight-charts 4.2** （TradingView 出品，专为金融图表优化）
- **three 0.165** （3D 曲面渲染）
- **@xterm/xterm 5.5** （终端样式日志）
- **swr 2.2** （轻量数据获取）
- **zustand 4.5** （状态管理预留）

## 目录结构

```
quant_ui_node/
├── package.json
├── tsconfig.json
├── next.config.js
├── tailwind.config.ts
├── postcss.config.js
├── next-env.d.ts
└── src/
    ├── app/
    │   ├── layout.tsx       # 全局布局
    │   ├── page.tsx         # / → HUD
    │   ├── view-a/page.tsx  # /view-a → 宏观-微观联动
    │   ├── view-b/page.tsx  # /view-b → 3D 曲面
    │   ├── view-c/page.tsx  # /view-c → YAML 配置
    │   └── logs/page.tsx    # /logs → 终端日志
    ├── components/
    │   ├── Sidebar.tsx
    │   ├── HUD.tsx
    │   ├── ViewA.tsx
    │   ├── ViewB.tsx
    │   ├── ViewC.tsx
    │   └── TerminalLogs.tsx
    ├── lib/
    │   ├── api.ts           # REST 客户端 + 类型契约
    │   └── useAlerts.ts     # WebSocket 双向推送 hook
    └── styles/
        └── globals.css
```
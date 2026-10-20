# arXiv Retrieval

EssayPod 下的 arXiv 自动推送引擎（v0.1）。提供两种订阅模式：

1. **分类 RSS**（`category_rss/`）：从 arXiv taxonomy 多选分类，按 RSS 拉取。
2. **关键词订阅**（`keyword_query/`）：维护关键词列表并多选启用，用 Query API 按周期拉取（不是即时搜索框）。

两者都支持：拉取时间（立即 / 定时）、周期（仅一次 / 每天 / 每两天 / 每周等）、每次篇数，以及「立即拉取」按钮。

测试推荐：周期选「仅一次（测试）」+ 拉取时间选「立即（测试）」+ 勾选「启用自动拉取」并保存；调度器约 15 秒内会跑一次，然后自动关闭启用开关。

## 启动

```bash
cd EssayPod/arxiv_retrieval
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

浏览器打开：<http://127.0.0.1:8787>

## 目录

```text
arxiv_retrieval/
  app.py                 # FastAPI + 简易 UI
  category_rss/          # 分类 RSS 拉取
  keyword_query/         # 关键词 Query API 拉取
  shared/                # 分类表、存储、调度
  static/                # Web UI
  data/                  # 运行时配置与结果（自动生成）
```

## 说明

- 分类数据来自 arXiv Category Taxonomy，共 8 大组约 155 个 subject class。
- 定时调度每 30 秒检查一次；到达本地时间（默认 `Asia/Shanghai`）且距上次运行超过周期时自动拉取。
- 结果保存在 `data/*_latest.json`，已见过的论文 ID 用于标记 `NEW`。
- arXiv RSS 周末（周六/周日）不更新；分类模式在 RSS 为空时会自动回退到 Query API。
- 请遵守 arXiv 礼貌访问：请求间隔约 3 秒。短时间频繁点击「立即拉取」可能触发 429 限流，稍后再试即可。

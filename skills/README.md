# 如何添加技能

每个技能一个目录，目录名即技能名（小写短横线）：

```
skills/
  my-skill/
    SKILL.md          # 必填
    workflows/        # 可选
    references/       # 可选
    templates/        # 可选
    scripts/          # 可选
```

`SKILL.md` 至少包含 YAML 头：

```yaml
---
name: my-skill
description: 做什么、何时用。第三人称。
---
```

可选字段：`category`（分类）、`tags`（逗号分隔）。

分类按技能实际产出，目前十一档：

| 分类 | 放什么 |
| --- | --- |
| 视听创作 | 短视频、播客、口播、分镜 |
| 图文创作 | 公众号、小红书、知乎、文案 |
| 学术工作 | 选题、论文、引用、开题 |
| 数据方法 | 建模、质检、可视化 |
| 商业金融 | 研报、估值、行情数据 |
| 工程研发 | 代码、前端、审计、DevOps |
| 办公文档 | PPT/Excel/Word/PDF |
| 营销增长 | 投放、SEO、活动策划 |
| 法律合规 | 合同、条款、合规 |
| 生活效率 | 简历、面试、OKR、学习卡片 |
| 技能工具 | 发现、检索、浏览器、生成器 |

放好文件后在仓库根目录执行 `npm run build`，再用 `npm run preview` 本地查看。推送到 `main` 后 GitHub Actions 会加密并发布到 GitHub Pages。

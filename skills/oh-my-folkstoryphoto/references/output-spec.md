# 项目输出与机器状态

## 目录

- [schema v4 新项目目录](#schema-v4-新项目目录)
- [用户分镜与 AI 分镜](#用户分镜与-ai-分镜)
- [review-state.json](#review-statejson)
- [批准哈希与回退](#批准哈希与回退)
- [发布 manifest](#发布-manifest)
- [旧项目兼容](#旧项目兼容)

## schema v4 新项目目录

```text
<主题目录>/
├── 01-故事脚本.md
├── 02-专业分镜表.md
├── 03-发布文件说明.md
├── 04-最终发布版-N图/
│   ├── 01.png ... NN.png
│   └── 最终发布总览.jpg
├── 05-参考素材/
│   ├── 01-角色参考/
│   ├── 02-地点母版/
│   └── 03-物件与设备参考/
├── 06-生成过程/
│   ├── 01-原始生成图/
│   ├── 02-返修记录/
│   └── 03-当前总览/
├── 07-制作资料/
│   ├── 01-创作方案.md
│   ├── 02-AI生成分镜.md
│   ├── 03-角色与视觉设定.md
│   ├── 04-出图提示词.md
│   ├── 05-参考图说明.md
│   └── 06-审查报告/
│       ├── 01-自审记录.md
│       ├── 02-返修报告.md
│       ├── 03-生成阻塞报告.md
│       └── 04-验收记录.md
└── 08-系统文件/
    ├── review-state.json
    ├── release-manifest.json
    ├── 01-生成请求/
    └── 02-状态备份/
```

所有编号使用连续的两位前缀。只有根目录三份 Markdown 面向用户；审查报告、请求快照和 JSON 不得散落在根目录。

按阶段延迟创建：初始化只创建 `01-故事脚本.md` 和 `08-系统文件/review-state.json`。分镜、参考、生成过程、报告和发布目录在进入对应阶段时再创建，不生成空报告。

## 用户分镜与 AI 分镜

`02-专业分镜表.md` 固定为五列，不得增删、改名或混入生产字段：

| 图号 | 画面拍什么 | 镜头怎么拍 | 人物在做什么 | 这张图要表达什么 |
|---|---|---|---|---|

每行必须填满，图号从 01 连续到 NN。字幕不在这里重复，统一保存在发布说明和 AI 分镜中。

`07-制作资料/02-AI生成分镜.md` 使用生产字段：

| 图号 | 唯一证据 | 字幕 | 拍摄来源 | 拍摄原因 | 受限机位 | 人物意识 | 设备/年代 | 成像结果 | 连续性引用 | 真实性风险 |
|---|---|---|---|---|---|---|---|---|---|---|

两张表的图数、顺序和核心画面含义必须对应。只有 AI 分镜用于提示词、参考计划、真实性自审和传输请求。

## review-state.json

新项目必须用初始化命令创建：

```bash
python3 <skill-dir>/scripts/review_state.py init-project \
  --project-dir /workspace/<主题目录>
```

故事阶段允许尚未确定图数：

```json
{
  "schema_version": 4,
  "project_dir": "..",
  "phase": "drafting",
  "planned_count": null,
  "max_repairs_per_item": 1,
  "artifacts": {
    "story": "01-故事脚本.md",
    "storyboard": "02-专业分镜表.md",
    "publication": "03-发布文件说明.md",
    "release_dir": "04-最终发布版-N图",
    "requests_dir": "08-系统文件/01-生成请求",
    "release_manifest": "08-系统文件/release-manifest.json"
  },
  "approvals": {},
  "images": [],
  "reference_jobs": [],
  "blocking_reasons": []
}
```

完整 `artifacts` 还必须包含目录树中定义的参考素材、生成过程、制作资料、报告和备份路径。v4 校验器拒绝被改名或移出编号目录的固定路径；`release_dir` 在登记图数后改为实际的 `04-最终发布版-N图`。

允许阶段：

```text
drafting
→ story_self_review
→ awaiting_story_approval
→ plan_self_review
→ awaiting_storyboard_approval
→ reference_self_review
→ awaiting_reference_approval
→ scene_self_review
→ awaiting_repair_approval
→ repairing
→ final_self_review
→ complete / needs_user
```

分镜登记后才创建连续图片任务：

```bash
python3 <skill-dir>/scripts/review_state.py register-storyboard \
  --state /project/08-系统文件/review-state.json --planned-count N
```

图片、参考任务、transport、batch、backend event、返修上限和自动恢复字段继续遵循 schema v3 契约。正式候选必须是精确竖版 4:5；`repair_count` 只能为 0 或 1。

## 批准哈希与回退

三次批准使用专用命令，通用 `transition` 不得绕过：

```bash
python3 <skill-dir>/scripts/review_state.py approve-story \
  --state /project/08-系统文件/review-state.json --user-approved

python3 <skill-dir>/scripts/review_state.py approve-storyboard \
  --state /project/08-系统文件/review-state.json --user-approved

python3 <skill-dir>/scripts/review_state.py approve-references \
  --state /project/08-系统文件/review-state.json --user-approved
```

- 故事批准保存当前 `01-故事脚本.md` 的 SHA-256。
- 分镜批准同时保存专业分镜和 AI 分镜的 SHA-256。
- 参考批准保存每个已通过参考候选的路径和 SHA-256。
- 后续预检先验证这些哈希；文件被修改时禁止继续生成。

用户修改已批准文件后运行：

```bash
python3 <skill-dir>/scripts/review_state.py reopen-gate \
  --state /project/08-系统文件/review-state.json --gate story|storyboard
```

命令先把当前状态保存到 `08-系统文件/02-状态备份/`，再清除下游批准和任务登记。已有图片文件不删除、不覆盖，但不再作为新方案的有效候选。

## 发布 manifest

v4 manifest 固定在 `08-系统文件/release-manifest.json`，其中来源路径仍相对于主题目录根部：

```json
{
  "images": [
    {
      "number": 1,
      "source": "06-生成过程/01-原始生成图/01.png",
      "focal_x": 0.5,
      "focal_y": 0.5
    }
  ]
}
```

最终阶段必须覆盖全部计划图号。v4 打包输出必须等于 `artifacts.release_dir`；manifest 位于系统目录不会改变来源路径的项目根目录语义。

## 旧项目兼容

- schema v1–v3 保持原目录、原路径和原状态读写规则。
- 不提供 v3→v4 自动迁移，不移动、不重命名已有作品。
- 未完成 v1 仍先显式迁移至 v2；未完成 v2 可显式迁移至 v3。
- 已完成 v2 保持只读；v3 项目继续使用 v3 自动恢复。
- `package_release.py` 同时接受 schema v2、v3 和 v4。

## 发布说明

`03-发布文件说明.md` 必须包含标题、形式、尺寸、逐图字幕、简介、节奏、AI 声明，以及：

- `原典/传说来源：`
- `怪谈式解读：`
- `本篇改编：`
- `虚构与 AI 声明：`

不得把网络推测写成历史事实、学术定论或原典唯一解释。

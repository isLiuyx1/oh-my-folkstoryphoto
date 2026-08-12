# 项目输出与机器状态

## 目录

- [schema v5 新项目目录](#schema-v5-新项目目录)
- [工作区目录](#工作区目录)
- [用户分镜与 AI 分镜](#用户分镜与-ai-分镜)
- [review-state.json](#review-statejson)
- [批准哈希与回退](#批准哈希与回退)
- [发布 manifest](#发布-manifest)
- [旧项目兼容](#旧项目兼容)

## 工作区目录

```text
<工作区>/
├── 01-进行中项目/
├── 02-已完成作品/
├── 03-代表作品/
├── 04-创作管理/
├── 05-参考素材/
├── 06-规范与模板/
├── 07-技能与工具/
└── 08-临时文件/
```

新项目必须创建在 `01-进行中项目/`。项目只有在状态为 `complete` 时才属于 `02-已完成作品/`；`needs_user`、待批准、自审和返修状态仍属于进行中。不得根据原文件夹名猜测完成状态。

现有工作区重排使用 `workspace_layout.py organize`，默认只输出计划；只有显式 `--apply` 才执行。命令拒绝活动生图锁，保留文本状态备份，更新绝对路径、跨项目引用和活动指针，并验证项目数量与旧路径残留。禁止手工拖动带 `review-state.json` 的项目。

## schema v5 新项目目录

```text
<主题目录>/
├── 00-真实性方案.md
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
│   ├── 00-真实性校准/
│   │   └── 真实性校准联系表.jpg
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
    ├── 02-状态备份/
    └── 03-真实性审查/
```

所有编号使用连续的两位前缀。根目录的真实性方案、故事、专业分镜和发布说明面向用户；审查报告、请求快照和 JSON 不得散落在根目录。

按阶段延迟创建：初始化只创建 `00-真实性方案.md` 和 `08-系统文件/review-state.json`。真实性批准后才创建 `01-故事脚本.md`；其余目录进入对应阶段后创建，不生成空报告。

## 用户分镜与 AI 分镜

`07-制作资料/01-创作方案.md` 必须包含参考作品拆解、八段式节拍表、默认或用户指定图数及其原因、证据载体计划和删图测试结论。该文件是生产资料，不增加根目录用户文档。

`02-专业分镜表.md` 固定为五列，不得增删、改名或混入生产字段：

| 图号 | 画面拍什么 | 镜头怎么拍 | 人物在做什么 | 这张图要表达什么 |
|---|---|---|---|---|

每行必须填满，图号从 01 连续到 NN。字幕不在这里重复，统一保存在发布说明和 AI 分镜中。

`07-制作资料/02-AI生成分镜.md` schema v5使用生产字段：

| 图号 | 唯一证据 | 画面原生文字 | 发布字幕 | 采集配置ID | 拍摄者 | 拍摄原因 | 受限机位 | 拍摄者入镜范围 | 设备可见性 | 人物意识 | 成像结果 | 连续性引用 | 校准角色 | 真实性风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

`设备可见性`只能为 `不可见`、`仅自然边缘/固定支架`、`由另一采集源拍到`。第三种必须在拍摄者入镜范围写第二拍摄源。`校准角色`必须各出现一次普通基线、最差拍摄条件、首次重大异常，其余填“无”。旧v4的十一列和十二列格式继续兼容，但新项目必须使用上述v5格式。

两张表的图数、顺序和核心画面含义必须对应。只有 AI 分镜用于提示词、参考计划、真实性自审和传输请求。

## review-state.json

新项目必须用初始化命令创建：

```bash
python3 <skill-dir>/scripts/workspace_layout.py init-project \
  --workspace /workspace --project-name <主题>
```

真实性阶段允许尚未确定图数：

```json
{
  "schema_version": 5,
  "project_dir": "..",
  "phase": "realism_self_review",
  "planned_count": null,
  "max_repairs_per_item": 1,
  "artifacts": {
    "realism_plan": "00-真实性方案.md",
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

完整 `artifacts` 还必须包含校准、真实性审查、参考素材、生成过程、制作资料、报告和备份路径。v5校验器拒绝被改名或移出编号目录的固定路径；`release_dir` 在登记图数后改为实际目录。

允许阶段：

```text
realism_self_review
→ awaiting_realism_approval
→ drafting
→ story_self_review
→ awaiting_story_approval
→ plan_self_review
→ awaiting_storyboard_approval
→ reference_self_review
→ awaiting_reference_approval
→ calibration_self_review
→ awaiting_calibration_approval
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

真实性、三次主创作与校准批准均使用专用命令，通用 `transition` 不得绕过：

```bash
python3 <skill-dir>/scripts/review_state.py approve-realism \
  --state /project/08-系统文件/review-state.json --user-approved

python3 <skill-dir>/scripts/review_state.py approve-story \
  --state /project/08-系统文件/review-state.json --user-approved

python3 <skill-dir>/scripts/review_state.py approve-storyboard \
  --state /project/08-系统文件/review-state.json --user-approved

python3 <skill-dir>/scripts/review_state.py approve-references \
  --state /project/08-系统文件/review-state.json --user-approved

python3 <skill-dir>/scripts/review_state.py submit-calibration \
  --state /project/08-系统文件/review-state.json \
  --contact-sheet /project/06-生成过程/00-真实性校准/真实性校准联系表.jpg

python3 <skill-dir>/scripts/review_state.py approve-calibration \
  --state /project/08-系统文件/review-state.json --user-approved
```

- 真实性批准保存 `00-真实性方案.md` 的SHA-256并创建故事文件。
- 故事批准保存当前 `01-故事脚本.md` 的 SHA-256。
- 分镜批准同时保存专业分镜和 AI 分镜的 SHA-256。
- 参考批准保存每个已通过参考候选的路径和 SHA-256。
- 校准批准保存三张正式候选和校准联系表的路径与SHA-256。
- 后续预检先验证这些哈希；文件被修改时禁止继续生成。

用户修改已批准文件后运行：

```bash
python3 <skill-dir>/scripts/review_state.py reopen-gate \
  --state /project/08-系统文件/review-state.json --gate story|storyboard
```

命令先把当前状态保存到 `08-系统文件/02-状态备份/`，再清除下游批准和任务登记。已有图片文件不删除、不覆盖，但不再作为新方案的有效候选。

## 发布 manifest

v4/v5 manifest固定在 `08-系统文件/release-manifest.json`，其中来源路径仍相对于主题目录根部：

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

最终阶段必须覆盖全部计划图号。v4/v5打包输出必须等于 `artifacts.release_dir`；manifest位于系统目录不会改变来源路径的项目根目录语义。

## 旧项目兼容

- schema v1–v3 保持项目内部目录、字段和状态读写规则。
- 不提供旧项目到v5的自动迁移。经用户明确授权，可用 `workspace_layout.py` 将完整项目目录重定位到工作区分类，但不改变内部schema。
- 未完成 v1 仍先显式迁移至 v2；未完成 v2 可显式迁移至 v3。
- 已完成 v2 保持只读；v3 项目继续使用 v3 自动恢复。
- `package_release.py` 同时接受 schema v2、v3、v4和v5。

## 发布说明

`03-发布文件说明.md` 必须包含标题、形式、尺寸、逐图字幕、简介、节奏、AI 声明，以及：

- `原典/传说来源：`
- `怪谈式解读：`
- `本篇改编：`
- `虚构与 AI 声明：`

不得把网络推测写成历史事实、学术定论或原典唯一解释。

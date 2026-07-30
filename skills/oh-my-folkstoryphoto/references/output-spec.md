# 项目输出与机器状态

## 项目目录
```text
<主题目录>/
├── 创作方案.md
├── 故事脚本.md
├── N图分镜.md
├── 角色与视觉设定.md
├── 出图提示词.md
├── 发布文件说明.md
├── 自审记录.md
├── 验收记录.md
├── review-state.json
├── release-manifest.json
├── 生成请求/
├── 角色参考/
├── 地点母版/
├── 原始生成图/
├── 返修记录/
└── 最终发布版-N图/
    ├── 01.png ... NN.png
    └── 最终发布总览.jpg
```
如目录已存在，检查内容并使用版本化目录。不得覆盖其他作品。

## 分镜字段
| 图号 | 唯一证据 | 字幕 | 拍摄来源 | 拍摄原因 | 受限机位 | 人物意识 | 设备/年代 | 成像结果 | 连续性引用 | 真实性风险 |
|---|---|---|---|---|---|---|---|---|---|---|
字幕不依赖图片内文字。调整图数时同步更新所有文件。

## review-state.json（schema v2）
```json
{
  "schema_version": 2,
  "project_dir": ".",
  "phase": "scene_self_review",
  "planned_count": 24,
  "max_repairs_per_item": 1,
  "artifacts": {
    "self_review": "自审记录.md",
    "acceptance": "验收记录.md",
    "release_manifest": "release-manifest.json"
  },
  "images": [
    {
      "number": 1,
      "status": "pending",
      "candidate": null,
      "hard_failures": [],
      "photo_red_flags": [],
      "repair_count": 0,
      "repair_mode": null,
      "repair_file": null,
      "final_source": null,
      "notes": "",
      "transport": {
        "backend": "built_in_imagegen",
        "route": null,
        "attempts_total": 0,
        "consecutive_failures": 0,
        "last_error": null,
        "last_error_type": null,
        "error_fingerprint": null,
        "backend_error_key": null,
        "prompt_sha256": null,
        "reference_sha256": [],
        "circuit_open": false,
        "probe_granted": false,
        "probe_in_flight": false
      }
    }
  ],
  "transport_backends": {},
  "fallback_authorizations": {},
  "blocking_reasons": []
}
```
允许阶段：`drafting`、`text_self_review`、`awaiting_plan_approval`、`reference_self_review`、`awaiting_reference_approval`、`scene_self_review`、`repairing`、`final_self_review`、`complete`、`needs_user`。

图片 `status` 只能是 `pending`、`generating`、`transport_blocked`、`review_pending`、`pass`、`needs_user`。初始化时必须建立 `planned_count` 个连续图号。

`pending`、`generating` 和 `transport_blocked` 可以没有候选文件；`review_pending`、`pass` 和内容审查后的 `needs_user` 必须有实际候选文件。`repair_count` 只能为 0 或 1，只记录实际候选图的内容返修。网络错误、超时和无候选由 `transport` 独立记录。

`transport_backends` 保存后端级熔断；`fallback_authorizations` 只由带 `--user-approved` 的命令写入。传输熔断不写入项目级 `blocking_reasons`，也不把阶段改为 `needs_user`。`needs_user` 保留给确实需要用户决策的内容审查或流程问题。

每个正式请求保存为 `生成请求/NN.json`。内置重试输入必须与该文件一致；已授权备用通道保存独立快照并记录与内置基线的差异。

使用 `review_state.py transition --state <file> --to <phase>` 更新阶段。跨越 `awaiting_plan_approval → reference_self_review` 或 `awaiting_reference_approval → scene_self_review` 时必须增加 `--user-approved`，表示已经收到用户明确批准。

工作区根目录可选创建 `.oh-my-folkstoryphoto-review.json`，内容为活动项目 `review-state.json` 的绝对或相对路径：

```json
{"state_file": "主题目录/review-state.json"}
```

可选 Stop hook 只读取这个指针；完成后保留指针也安全，因为 `complete`、`needs_user` 和两个 `awaiting_*` 阶段允许停止。

## 发布 manifest

```json
{
  "images": [
    {
      "number": 1,
      "source": "原始生成图/01.png",
      "focal_x": 0.5,
      "focal_y": 0.5
    }
  ]
}
```

制作期 manifest 可以是已通过图片的任意唯一子集，来源只能是状态中 `status: "pass"` 的 `final_source`。进入 `final_self_review` 或 `complete` 后，manifest 必须覆盖从 1 到 `planned_count` 的全部图号；焦点为可选 0–1 数值。

## v1 兼容与迁移

`review_state.py validate` 继续读取 schema v1。只对用户明确指定的未完成项目运行：

```bash
python3 <skill-dir>/scripts/review_state.py migrate \
  --state /path/to/review-state.json \
  --to-version 2 \
  --planned-count N
```

先加 `--dry-run` 验证迁移。不得扫描、批量迁移或改写已完成项目。

## 发布说明

必须包含标题、形式、尺寸、逐图字幕、简介、节奏、AI 声明，以及：

- `原典/传说来源：`
- `怪谈式解读：`
- `本篇改编：`
- `虚构与 AI 声明：`

不得把网络推测写成历史事实、学术定论或原典唯一解释。

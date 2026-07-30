---
name: oh-my-folkstoryphoto
description: Create complete Chinese folk-story, legend, mystery, horror, or wonder photo-carousel packages from a user-provided inspiration source. Use for 民间故事、传说、怪谈、志怪、香巴拉、桃花源、手机伪纪录、AI 仿真照片、抖音或小红书图文轮播 requests that need source research, adaptation boundaries, a 24–27-frame evidence chain, period-plausible candid capture, character/location continuity, built-in image generation, structured self-review, one repair round, release packaging, and two explicit user approval gates.
---

# oh-my-FolkStoryPhoto

把用户提供的网址、文章、文件、图片、传说名称或文字灵感，制作成可发布的中文伪纪录图文系列。默认 24–27 张 4:5 无字图片；未指定时采用纪实悬疑。

## 不可违反的规则

- 要求用户提供灵感来源；先区分原典、现代解读、视觉参考、强制设定和本篇虚构。
- 默认在当前工作区创建独立主题目录。检查同名目录并使用版本化名称；不得覆盖或移动已有项目。
- 保留两次停止门槛：
  1. 用户明确确认完整文字方案后，才可生成参考图。
  2. 用户明确确认角色参考图和关键地点母版后，才可生成正式分镜。
- 沉默、含糊肯定和“先看看”不算批准。等待用户时将状态设为对应 `awaiting_*`，允许任务停止。
- 图片默认且仅使用 `$imagegen` 内置模式。每个独立场景单独调用一次；不得把不同分镜作为同一提示词的批量变体。
- 内置生图调用必须省略无值的可选字段：全新生图只传 `prompt`；参考生成只传 `prompt` 与真实存在的 `referenced_image_paths`；不得传 `null`、空数组、无效路径或同时传两种图片上下文机制。
- 将网络错误、超时和未返回候选文件视为传输失败。调用前使用 `transport_guard.py preflight` 固化提示词和参考图指纹；传输失败不计入图片返修次数。
- 同一分镜连续 3 次传输失败时只熔断该分镜并继续其他独立分镜；两个分镜出现相同后端错误时再熔断该后端。跨对话重试必须由用户批准，且每次只放行一次探测。
- 不得在重试时删除必需参考图、改写已批准提示词或降低连续性要求。不得自行切换 CLI、API 或其他模型；备用通道必须先取得用户明确授权并记录输入差异。
- 项目图片必须复制回工作区，不能只留下生成缓存路径。
- 原图、修正版和最终发布图分开保存；返修不得覆盖原图。
- 只有实际返回候选图且内容审查失败才消耗返修次数。每个失败项最多自动修复一次；二次审查仍失败时设为 `needs_user`，不得包装成合格成片。

## 开始前读取

必须完整读取：

- [workflow.md](references/workflow.md)：状态、确认门槛、生成和失败处理。
- [capture-authenticity.md](references/capture-authenticity.md)：年代设备档案、可信拍摄事件和普通相册测试。
- [visual-language.md](references/visual-language.md)：证据链比例、题材分型和提示词骨架。
- [self-review.md](references/self-review.md)：成稿、参考图、单图和全篇自审返修闭环。
- [output-spec.md](references/output-spec.md)：项目目录、文档、状态文件和 manifest。
- [quality-checklist.md](references/quality-checklist.md)：硬失败、摄影红旗和发布验收。

执行图片生成或编辑前还必须完整读取 `$imagegen` 的 `SKILL.md`。

## 工作流

### 1. 研究与改编边界

- 读取全部用户材料；网址、指定页面、最新资料或陌生传说按需浏览。
- 把传统来源、民间/网络推测和本篇新增虚构分开记录。
- 涉及宗教、族群、真实灾难、现实人物或现实路线时，先确定不可误导的改编边界。
- 建立全篇“拍摄设备档案”，保证年代和介质可信。

### 2. 文字方案与第一次确认

创建 `review-state.json`，阶段从 `drafting` 进入 `text_self_review`。完成：

- `创作方案.md`
- `故事脚本.md`
- `N图分镜.md`
- `角色与视觉设定.md`
- `出图提示词.md`
- `发布文件说明.md`
- `自审记录.md`

每张分镜必须包含唯一证据、字幕、拍摄来源、原因、受限机位、人物意识、设备/年代、成像结果、连续性引用和真实性风险。按 [self-review.md](references/self-review.md) 自审；失败项修订一次并复审。通过后设为 `awaiting_plan_approval`，展示摘要并停止。

### 3. 参考图与第二次确认

方案获批后进入 `reference_self_review`：

- 先生成主要角色、固定服装/道具、关键车辆或设备、重复地点和最终奇观母版。
- 参考图用于锁定身份与空间，不强制伪装成最终手机抓拍。
- 将选中结果复制到 `角色参考/` 或 `地点母版/`，保留内置生成原文件。
- 检查身份、服装、地点结构和年代合理性。通过后设为 `awaiting_reference_approval`，展示并停止。

### 4. 正式生图、自审与一次返修

参考图获批后逐镜生成。对每张现实镜头先写清拍摄事件，再选择少量场景适配的构图和成像结果。生成后必须同时查看单图和联系表。

- 按 [workflow.md](references/workflow.md) 的合法调用范式构造参数，并用 `transport_guard.py preflight` 校验和固化全部必需参考图。
- 遇到传输失败时用 `record-failure` 持久化累计次数；单图熔断后跳过该图继续其他独立分镜，不要增加 `repair_count`。
- 用户要求再次尝试已熔断分镜时，先运行 `resume-probe --user-approved`，只执行一次探测；不得重新开始三次重试。
- 内置后端被熔断时停止新的批量调用；只有批准探测成功或用户明确授权备用通道后才恢复。
- 硬失败或三个及以上摄影红旗必须返修。
- 拍摄者、视线、机位、构图或整体摄影语言错误时整图重生成。
- 身份和空间正确、仅有局部文字或结构瑕疵时使用编辑返修。
- 每张最多一次自动返修；编辑时重复全部不变量。
- 二次检查仍失败则设为 `needs_user`，保留版本和原因，不加入发布 manifest。

### 5. 全篇验收与发布

进入 `final_self_review`，执行普通相册测试、连续性、证据链、设备一致性、字幕对应和最终证据检查。建立发布 manifest 后运行：

```bash
python3 <skill-dir>/scripts/review_state.py validate --state /path/to/review-state.json
python3 <skill-dir>/scripts/package_release.py --state /path/to/review-state.json --manifest /path/to/release-manifest.json --output-dir /path/to/最终发布版-N图
```

只有全部计划分镜通过且状态校验通过才可打包。完成 `验收记录.md` 和 `自审记录.md`，将状态设为 `complete`。最终交付成片目录、总览、故事、分镜、最终提示词、返修记录、灵感说明和内置生图路径说明。

## 覆盖策略

- 用户指定的气质、图数和视觉参考优先，但不得隐式取消两次确认门槛或单轮返修上限。
- 非恐怖题材不添加鬼脸、尸体、邪教或惊吓；恐怖题材默认克制，正面怪物最多一次。
- 用户禁止联网时只用其材料，并注明未外部核验。
- 优化只应用于新项目；不得批量改写现有作品。

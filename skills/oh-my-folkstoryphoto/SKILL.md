---
name: oh-my-folkstoryphoto
description: Create complete Chinese folk-story, legend, mystery, horror, or wonder photo-carousel packages from a user-provided inspiration source. Use for 民间故事、传说、怪谈、志怪、香巴拉、桃花源、手机伪纪录、AI 仿真照片、抖音或小红书图文轮播 requests that need source research, a user-editable story-first workflow, separate public and AI production storyboards, a 24–27-frame evidence chain, period-plausible candid capture, character/location continuity, built-in image generation, structured self-review, one repair round, release packaging, three main approval gates, and explicit repair approval.
---

# oh-my-FolkStoryPhoto

把用户提供的网址、文章、文件、图片、传说名称或文字灵感，制作成可发布的中文伪纪录图文系列。默认 24–27 张 4:5 无字图片；未指定时采用纪实悬疑。

## 不可违反的规则

- 要求用户提供灵感来源；先区分原典、现代解读、视觉参考、强制设定和本篇虚构。
- 默认在当前工作区创建独立主题目录。检查同名目录并使用版本化名称；不得覆盖或移动已有项目。
- 保留三次主创作停止门槛：
  1. 只展示 `01-故事脚本.md`；用户明确确认故事后，才可规划分镜。
  2. 用户明确确认 `02-专业分镜表.md` 后，才可生成参考图。
  3. 用户明确确认角色参考图和关键地点母版后，才可生成正式图片。
- 返修报告仍需专项批准。传输恢复、参考降级、批次切换和单项跳过不增加确认。
- 沉默、含糊肯定和“先看看”不算批准。等待用户时将状态设为对应 `awaiting_*`，允许任务停止。
- 图片默认使用 `$imagegen` 内置模式。每个独立场景单独调用一次；不得把不同分镜作为同一提示词的批量变体。只有用户明确授权并完成状态登记后，才可使用本技能规定的备用通道。
- 所有正式图必须是竖版 4:5。提示词必须明确写出 `vertical 4:5`（或“竖版 4:5”），不得含 `landscape`、`3:2`、`16:9` 等冲突画幅。模型原始返回若是竖版 2:3，可用 `normalize_candidate.py` 在构图安全区内非破坏性裁为精确 4:5；横图必须重生成，禁止靠大幅裁切补救。`record-success` 只接受精确 4:5 候选。
- 内置生图调用必须省略无值的可选字段：全新生图只传 `prompt`；参考生成只传 `prompt` 与真实存在的 `referenced_image_paths`；不得传 `null`、空数组、无效路径或同时传两种图片上下文机制。
- 将网络错误、超时和未返回候选文件视为传输失败。调用前使用 `transport_guard.py preflight` 固化提示词和参考图指纹；传输失败不计入图片返修次数。
- 所有生图严格串行。`preflight` 自动创建或滚动批次；每批最多成功 3 张或持续 15 分钟，不因批次切换请求用户确认。传输失败不得结束批次或整个任务。
- 新正式请求以 0–1 张参考图为常态，最多 2 张。无重复人物或地点的独立环境镜头使用 0 张；有人物连续性时优先一张只含本镜必要人物的自然同框参考；第二张仅补主参考缺失的关键地点或物件。三张以上必须说明不可替代性并取得用户明确授权。
- `timeout` 或 `no_candidate` 自动降级参考输入并立即固化下一档请求，无需用户确认：双参考依次使用原图、1024×1280/JPEG 88 单文件参考板、从原始来源重建的 768×960/JPEG 80 参考板；单参考依次使用原图、最长边 1024/JPEG 88、最长边 768/JPEG 80。`network_error` 不改变输入。
- 自动派生只允许全图缩放、JPEG 压缩或双来源拼板；不得自动裁人物、抠图、删锚点或递归压缩旧参考板。保留原图、顺序、角色、提示词和全部哈希。三档仍无候选时标为 `transport_blocked`，记录后继续其他任务。
- 任何派生参考都必须先核对项目的 `07-制作资料/05-参考图说明.md`、文件名和本镜出镜表；不得仅凭外观猜测人物身份。预检为每张输入增加同序 `--reference-role`，写清人物姓名和锚定内容；角色数量或身份不一致时不得调用生图。
- 正式提示词只展开本镜实际出现的人物、地点、道具和摄影约束；不要复制全篇未出现设定。双参考和参考板请求必须包含固定参考板安全句；自动恢复不改提示词。
- 双独立参考的正式原图调用最多等待 8 分钟；0–1 张自然参考、单文件参考板和返修调用最多等待 10 分钟。达到对应硬超时仍无候选时终止并记录 `timeout`，不得无限等待。
- 若调用被用户中止、回合中断或工具超时，先确认项目目录和生成缓存均无候选，再按任务运行 `recover-interrupted --number NN` 或 `recover-interrupted --reference-id ID` 并附 `--confirm-no-candidate`；不得让 `generating` 永久遗留。
- 核对生成缓存时只能采用当前任务目录或工具明确返回的路径；不得仅凭修改时间从全局缓存认领文件。任何迟到候选都必须先查看并确认与当前分镜内容相符，才能登记成功。
- 同一任务累计 3 次传输失败且没有下一档自动恢复时，只阻塞该任务并继续其他参考图、分镜、返修或后续批次。两个任务出现相同后端错误时只记录健康告警，不熔断后端。
- 不得在重试时删除必需参考图、改写已批准提示词或降低连续性要求。不得自行切换 CLI、API 或其他模型；备用通道必须先取得用户明确授权并记录输入差异。
- Pro/Team 用户明确授权订阅备用通道后，可使用 `subscription_image_bridge.py` 为单张分镜启动全新、临时、ChatGPT 登录态的 `codex exec`。必须保留原请求与参考指纹，最多 2 张参考，固定项目输出和日志，且不得把它描述为独立 API 路由。
- 重试必须用 `materialize-prompt` 或 `materialize-reference-prompt` 从既有请求快照逐字节恢复提示词；不得用会追加换行的文本提取方式重建。
- 已获用户批准的返修请求同样自动降级参考输入；内容返修批准门槛和一次返修上限不变。
- 项目图片必须复制回工作区，不能只留下生成缓存路径。
- 原图、修正版和最终发布图分开保存；返修不得覆盖原图。
- 先生成并审查全部正式原图，不得边生成边返修。所有原图齐备后生成《返修报告》并停在 `awaiting_repair_approval`；只有用户批准的图号才能统一返修。每个批准项最多返修一次，二次审查仍失败时设为 `needs_user`。

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

### 2. 故事与第一次确认

用 `review_state.py init-project` 创建 schema v4 项目。第一阶段只完成 `01-故事脚本.md`，不得提前生成分镜、发布说明、提示词或参考任务。故事自审通过后进入 `awaiting_story_approval` 并停止；用户可直接编辑该文件。收到明确批准后运行 `approve-story --user-approved`，以用户当前文件内容为准保存哈希。

### 3. 分镜与第二次确认

故事批准后生成：

- 根目录 `02-专业分镜表.md`：只含图号、画面拍什么、镜头怎么拍、人物在做什么、这张图要表达什么。
- 根目录 `03-发布文件说明.md`。
- `07-制作资料/` 内的创作方案、AI 生成分镜、视觉设定、提示词和参考说明。

AI 生成分镜保留唯一证据、字幕、拍摄来源、原因、受限机位、人物意识、设备/年代、成像结果、连续性引用和真实性风险。运行 `register-storyboard --planned-count N` 后自审；通过后进入 `awaiting_storyboard_approval` 并停止。用户批准时运行 `approve-storyboard --user-approved`，同时固化用户分镜与 AI 分镜哈希。

### 4. 参考图与第三次确认

分镜获批后进入 `reference_self_review`：

- 先生成主要角色、固定服装/道具、关键车辆或设备、重复地点和最终奇观母版。
- 用 `review_state.py register-reference-job` 登记每项，再用 `reference-preflight`、`record-reference-success|failure` 执行，与正式分镜共享自动批次和参考降级。候选自审使用 `record-reference-review`；首次失败自动固化一版 regenerate 请求，第二版仍失败才进入 `needs_user`。
- 参考图用于锁定身份与空间，不强制伪装成最终手机抓拍。
- 将选中结果复制到 `05-参考素材/01-角色参考/`、`02-地点母版/` 或 `03-物件与设备参考/`，保留内置生成原文件。
- 检查身份、服装、地点结构和年代合理性。通过后设为 `awaiting_reference_approval`，展示并停止。
- 收到明确批准后运行 `approve-references --user-approved`，保存批准候选哈希并进入正式生图。

### 5. 正式生图、自审、返修报告与统一返修

参考图获批后逐镜生成。对每张现实镜头先写清拍摄事件，再选择少量场景适配的构图和成像结果。生成后必须同时查看单图和联系表。

- 按 [workflow.md](references/workflow.md) 的合法调用范式构造参数，并用 `transport_guard.py preflight` 校验和固化最少必要参考图。
- 首次固化前确认提示词明确要求竖版 4:5；返回后先核验方向与构图，必要时运行 `normalize_candidate.py` 将竖版原始输出裁成精确 4:5，再复制/登记项目候选。横版输出属于硬失败，必须重生成。
- 调用前按项目参考清单逐一核对输入人物，并为每个 `--reference` 提供同序 `--reference-role`。同时查看 `prompt_summary`；高风险长提示词必须删去本镜未出现的全局设定后再首次固化。
- 用 `batch-status` 检查在途、自动恢复等级、冷却、阻塞项和下一步。`preflight` 自动创建/滚动批次；一次只允许一个内置生图调用。
- 遇到传输失败时用 `record-failure` 持久化累计次数；单图熔断后跳过该图继续其他独立分镜，不要增加 `repair_count`。
- 自动降级成功固化时立即重试当前项；网络错误或无可降级输入时按冷却时间延后该项，同时继续其他可运行项。
- 单项耗尽恢复阶梯后跳过。全部可运行项结束后运行 `prepare-blocked-report`，统一报告缺图；不得提前打断任务询问用户。
- 硬失败或三个及以上摄影红旗使用 `queue-repair` 登记，不立即发起返修。
- 全部原图生成并审查后运行 `prepare-repair-report`，向用户展示报告并停止。收到明确批准后运行 `authorize-repairs --user-approved`；只返修获批图号。
- 获批返修调用使用 `preflight --repair-mode edit|regenerate` 保存独立请求；复审通过后使用 `review_state.py mark-pass` 登记最终来源。
- 精确局部编辑默认只携带编辑目标和确实参与被改区域重建的参考图；已在编辑目标中清晰可见且要求保持不变的内容写入不变量，不重复上传无关母版。请求快照固化后不得静默删减参考。
- 对新请求执行参考预算：优先 `0–1` 张、单张最长边不超过约 1024 px、总输入尽量不超过约 1.5 MiB。超出预算不等于禁止调用，但必须先说明连续性收益和长尾风险；不得为了达标改写已批准原图，只能创建可追溯派生文件。
- 新正式提示词统一说明：参考输入只用于身份、物件和地点连续性；若输入为双区域参考板，不得复制分栏、间隔、源构图、重复人物或额外人物。
- 拍摄者、视线、机位、构图或整体摄影语言错误时整图重生成。
- 身份和空间正确、仅有局部文字或结构瑕疵时使用编辑返修。
- 每张最多一次自动返修；编辑时重复全部不变量。
- 二次检查仍失败则设为 `needs_user`，保留版本和原因，不加入发布 manifest。

### 6. 全篇验收与发布

进入 `final_self_review`，执行普通相册测试、连续性、证据链、设备一致性、字幕对应和最终证据检查。建立发布 manifest 后运行：

```bash
python3 <skill-dir>/scripts/review_state.py validate --state /project/08-系统文件/review-state.json
python3 <skill-dir>/scripts/package_release.py --state /project/08-系统文件/review-state.json --manifest /project/08-系统文件/release-manifest.json --output-dir /project/04-最终发布版-N图
```

只有全部计划分镜通过且状态校验通过才可打包。完成 `验收记录.md` 和 `自审记录.md`，将状态设为 `complete`。最终交付成片目录、总览、故事、分镜、最终提示词、返修记录、灵感说明和内置生图路径说明。

## 覆盖策略

- 用户指定的气质、图数和视觉参考优先，但不得隐式取消三次主创作确认门槛或单轮返修上限。
- 非恐怖题材不添加鬼脸、尸体、邪教或惊吓；恐怖题材默认克制，正面怪物最多一次。
- 用户禁止联网时只用其材料，并注明未外部核验。
- 优化只应用于新项目；不得批量改写现有作品。

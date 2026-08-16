---
name: spec-design-implementation
description: 在开发需求资料时通过 SpecWeaver 缓存清单发现已确认设计，先查看项目内设计预览图，再按组件或区域查询缓存中的规范化设计事实并据此还原页面。用户要求“开发这个需求”“按需求文档实现”“照设计稿还原页面”或修改带有设计稿来源资料的前端界面时使用。负责设计证据消费与视觉回归，不负责重新收集需求。
---

# 设计实现

## 边界

- 只在用户明确要求开发或修改代码时使用；资料收集阶段不得提前进入本 Skill。
- 需求任务决定业务和交互，API 文档决定契约，设计稿决定可见视觉事实。
- 预览图用于理解整体，规范化 JSON 用于查精确值；不要通读或复制整个大型 JSON。
- 不重新选择设计范围；上下文缺失或存在多个匹配需求时暂停并询问，不静默猜测。

## 1. 自动发现设计上下文

1. 从用户给出的任务 ID、需求名称、`requirement.md` 路径或当前任务目录定位需求
   资料目录，从 `requirement-raw.md` 读取任务 URL，并调用
   `requirement_get_manifest(url, output_dir)` 定位用户缓存中的收集清单。
2. 只有一个匹配项时自动采用，不要求用户重复提供设计稿链接。
3. 多个匹配项无法根据任务 ID 或路径排除时，列出候选并暂停。
4. 读取清单中的 `design.items`，验证预览图、结构文件和切图目录真实存在；缺失时
   报告具体文件，不伪造。

## 2. 先看图，再查事实

对每张与开发范围相关的设计：

1. 先打开 `preview_file`，确认页面、状态、区域和整体视觉关系。
2. 根据当前实现范围定位组件或区域，不把整份设计 JSON 加载进上下文，也不把图层树
   贴进对话或文档。
3. 使用 `scripts/query_design.py` 查询节点摘要：

```bash
python3 <skill-dir>/scripts/query_design.py <design-json> summary
python3 <skill-dir>/scripts/query_design.py <design-json> search --query "按钮文案"
python3 <skill-dir>/scripts/query_design.py <design-json> node --id "<node-id>"
python3 <skill-dir>/scripts/query_design.py <design-json> region --x 0 --y 300 --w 375 --h 120
python3 <skill-dir>/scripts/query_design.py <design-json> measure --from-id "<node-a>" --to-id "<node-b>"
```

字段是统一 schema：`text.content`、相对画板的 `frame.x/y/w/h`、`fill`、`layout.mode`
（`row|column`）、切图 `asset`。不要再用 `layers`、`textInfo`、`frame.left`。

4. 一次查询一个组件或区域所需的整组属性，避免按颜色、间距、圆角逐项碎查。
5. 对颜色、字体、字号、尺寸、间距和切图有精确值需求时必须查询，不能只凭预览图估算。
6. 查询结果仍不明确时再读取目标节点附近的小段 JSON。

## 3. 实现与回归

1. 先遵循目标仓库现有组件、样式和资源约定，再应用查询到的设计事实。
2. 优先使用缓存清单与设计结构指向的真实切图，不重画已有资产。
3. 完成页面后运行与改动相称的检查，并在条件允许时生成实现截图。
4. 将实现截图与设计预览图比较；对差异区域再次按组件、节点或坐标查询后修正。
5. 无法自动截图时明确说明未完成视觉对比，不把代码检查通过描述成还原验证通过。

完成后报告采用的设计上下文、查询过的关键区域、验证结果和仍无法确认的视觉细节。

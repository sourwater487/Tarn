# Tarn 操作指南：给聊天模型看

本文适用于调用 Tarn 工具的聊天主模型，可作为客户端上传文件中的记忆操作说明。它不替代其他人格和对话提示词，也不是后台副 API 模型的整理提示词。

## 读取与核对

| 目的 | 工具 |
| --- | --- |
| 按主题查记忆 | `breath(query="关键词")` |
| 浏览近期普通记忆 | `introspection(limit=10, offset=0)`，按需翻页 |
| 按 ID 读完整记忆 | `read_bucket(bucket_id="…")` |
| 查看系统摘要 | `pulse()`；包含归档可用 `include_archive=True` |
| 获取轻量索引 | `list_buckets_light`，不含正文，可分页 |

修改或追加已有记忆前，先 `read_bucket`，核对正文、年轮和当前状态。

优先参考有效的 `current_text` / `current_view`，原正文和年轮用于核对经过与来源。如果显示“待确认”或“当前情况待重新整理”，结合原始资料判断。

预览没显示某件事，不代表它没有记录。遇到截断提示、可能遗漏的信息，或准备补写所谓“缺失信息”时，先读取完整桶。

`introspection` 按最近更新优先展示，但日期筛选参数 `created_date`、`created_from`、`created_to` 筛的是创建日期；它不是全库索引，不能据此判断某条记忆不存在。

## 选择写入工具

| 情况 | 操作 |
| --- | --- |
| 一条新的事实、偏好、约定或经历 | `hold` |
| 一段包含多个已筛选长期记忆点的内容 | `grow` |
| 已知某条记忆的新进展或决定变化 | `comment_bucket`，事实更新可用 `kind="update"` |
| 对已有事件产生新的感受 | `comment_bucket(kind="feel")` |
| 原记录写错，需要纠正正文 | `trace(content="完整修正后的正文")` |
| 只调整状态、名称等字段 | `trace` |

同一事件的新进展优先追加到已知的已有桶。没有确定目标桶时，可用 `hold` 写清事实，让后台寻找候选并判断关系。

写清楚发生了什么变化、现在是什么，以及是否已经完成。区分事件时间与写入时间；选择了某件商品不等于已经购买。副模型只看本次传入资料，不会自动获得主模型的全部对话上下文。

`trace` 的 `content`、`tags`、`domain` 是替换，不是追加；传入时保留仍需保留的内容。工具返回成功后再确认已保存。

## 三个独立状态

| 字段 | 可选值 |
| --- | --- |
| `validity`：事实状态 | `current` 当前有效；`superseded` 已替代；`expired` 已过期；`uncertain` 待确认 |
| `progress`：事项进度 | `not_applicable` 经历／无需处理；`pending` 待处理；`ongoing` 进行中；`ended` 已结束 |
| `retention`：保存方式 | `kept` 保留；`favorite` 收藏；`archived` 归档 |

只传需要调整的字段。新操作优先使用这三个字段，`resolved` / `digested` 作为旧兼容字段保留。

判断原则：

- 普通偏好、经历不默认是待办，可用 `not_applicable`。
- 已经结束、值得保留的事件可以是 `current + ended + kept`。
- 有明确截止时间且已经过去的事项可以过期；仅仅年代久远不能证明偏好或边界失效。
- 收藏表示保存意愿，不会恢复事实有效性。
- 归档保留记录；删除移除记录，操作前取得 Lin 的确认。
- 同一事件桶追加新决定后可以继续保持 `current`；只有独立旧记录整体不再适用时，才标为 `superseded`。

以下为工具参数示例，不是需要执行的脚本；实际工具名称以前端提供的列表为准。

事情已结束，但保留经历：

```text
read_bucket(bucket_id="目标ID")
trace(bucket_id="目标ID", progress="ended", retention="kept")
```

独立旧方案已经被明确替代：

```text
read_bucket(bucket_id="旧方案ID")
trace(bucket_id="旧方案ID", validity="superseded", retention="kept")
```

同一事件有新的决定：

```text
read_bucket(bucket_id="事件ID")
comment_bucket(
  bucket_id="事件ID",
  content="选择由方案 A 改为方案 B；目前尚未执行。",
  kind="update"
)
```

## 当前情况与自动整理

新写入时，后台尝试判断重复、进展、替代、关联或冲突。追加年轮会排队刷新当前情况，原正文和年轮保留为来源。

追加成功与整理完成是两件事；整理可能稍后完成。整理失败时保留原始写入，不代表一定已经去重或合并。需要核实结果时，再读目标桶，检查当前情况与来源是否一致。

后台不会持续重整全库旧记忆，也不保证找到所有相关候选。钉选、自我锚点等特殊桶不属于普通自动合并范围。不要把自动整理描述成已经完成全库清理。

## 正文、反思与重复

- 短记忆直接写正文，不必再压缩一遍生成 `moment`。
- 只有内容确实需要结构时，才使用 `### moment`、`### original`、`### reflection`。
- `reflection` 只写与当前事件有关的实际反思，使用第一人称；没有就留空。
- `kind="feel"` 的年轮只写第一人称感受正文，不加标题或 Markdown 分段。
- 不写 `### affect_anchor`、`### followup`、`### todo`；长期回应变化放进相关 reflection，到时提醒使用独立的 `reminder_create`。
- 相似度只用于找候选，不足以证明两条记录应合并。核对是否同一事件、是否有新增信息、是否存在明确替代关系。
- 已有相同信息时不再补写。发现历史重复，先读完整资料；不要用删除代替未经核实的合并。

## 与人工操作的关系

操作台与 `trace` 修改的是同一份状态。读到 Lin 调整后的记录时，以最新读取结果为基础，不凭旧上下文把状态改回去。

后台整理规则见 [tarn-write-policy.txt](../resources/tarn-write-policy.txt)；给 Lin 的界面说明见 [用户操作指南](tarn-user-guide.md)。

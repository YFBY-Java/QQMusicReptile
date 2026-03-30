# QQMusicReptile

- `src/qq_music_profile_fetcher.py`：原始脚本，不做修改。
- `src/qq_music_profile_fetcher_configurable.py`：QQ 音乐主页抓取主脚本，支持批量查询、筛选、输出简化结果，并可联动 QQ 等级查询。
- `src/qq_level_query.py`：独立 QQ 等级查询脚本。
- `config/qq_music_profile_fetcher_config.json`：QQ 音乐主页抓取配置。
- `config/qq_level_query_config.json`：QQ 等级查询配置。

## 功能

- QQ 音乐主页支持多 Cookie 轮询。
- QQ 等级查询支持多 Cookie 轮询，失败后自动切换下一个 Cookie 重试。
- 支持直接指定 QQ 号，或按规则批量生成 QQ 号。
- 支持按字段筛选结果，每个字段都有独立开关。
- 结果输出到指定目录，不存在会自动创建。

## QQ 音乐主页抓取

配置文件：`config/qq_music_profile_fetcher_config.json`

### 1. Cookie 配置

```json
"cookies": {
  "multiline": "",
  "items": [
    "uin=o000000001; qm_keyst=your_cookie_1;",
    "uin=o000000002; qm_keyst=your_cookie_2;"
  ]
}
```

- `items`：Cookie 列表。
- `multiline`：多行字符串写法，每行一个 Cookie。
- 主脚本会按顺序轮询 Cookie；单个 QQ 失败时会切到下一个 Cookie 继续重试。

### 2. QQ 号直接配置

```json
"qq_numbers": [
  "12345678",
  "87654321"
]
```

- 会和 `qq_generator` 生成的结果合并，并自动去重。

### 3. QQ 号批量生成

```json
"qq_generator": {
  "enabled": true,
  "prefix": "12",
  "suffix": "88",
  "pad_width": 4,
  "start": 1,
  "end": 20,
  "step": 1
}
```

生成规则：

`QQ = prefix + 中间数字补零到 pad_width 位 + suffix`

### 4. 请求控制

```json
"request": {
  "retries": 2,
  "max_count": 0
}
```

- `retries`：单个 QQ 请求失败后的重试次数。
- `max_count`：最多处理多少个 QQ。
- `0` 表示不限制。

### 5. 休眠配置

```json
"sleep": {
  "seconds": 1.5,
  "jitter_seconds": 0.5
}
```

- 实际休眠时间为 `seconds + 随机抖动`。

### 6. 字段筛选

```json
"filters": {
  "ip_location": {
    "enabled": true,
    "contains": "广东"
  },
  "gender": {
    "enabled": false,
    "equals": "男"
  },
  "fans_num": {
    "enabled": false,
    "in": ["100", "1000"]
  }
}
```

- 每个字段都是独立配置项。
- `enabled`：该字段筛选是否生效。
- `equals`：完全匹配。
- `contains`：包含指定字符串。
- `not_contains`：不包含指定字符串。
- `regex`：正则匹配。
- `in`：命中给定列表之一。

可筛选字段示例：

- `qq_number`
- `name`
- `ip_location`
- `fans_num`
- `visitor_num`
- `gender`
- `constellation`
- `encrypt_uin`
- `qq_music_vip_level`

示例：

```json
"filters": {
  "ip_location": {
    "enabled": true,
    "contains": "广东"
  }
}
```

```json
"filters": {
  "gender": {
    "enabled": true,
    "equals": "男"
  }
}
```

如果不想筛选：

```json
"filters": {}
```

脚本也兼容旧版数组写法：

```json
"filters": [
  {
    "field": "ip_location",
    "contains": "广东"
  }
]
```

### 7. 输出配置

```json
"output": {
  "directory": "output",
  "file": "qq_music_results.json",
  "fields": [
    "qq_number",
    "qq_level",
    "qq_nickname",
    "qq_music_nickname",
    "ip_address",
    "qq_music_vip_level",
  ],
  "include_raw_profile_data": false
}
```

- `directory`：结果输出目录，不存在会自动创建。
- `file`：输出文件名；如果传绝对路径，也会自动创建上级目录。
- `fields`：控制最终输出哪些字段；留空时输出完整结果。
- `include_raw_profile_data`：是否保留完整原始资料。

## 运行

```bash
python src/qq_music_profile_fetcher_configurable.py
```

## 输出结构

- `summary`：统计信息。
- `matches`：筛选后的命中结果。
- `failures`：失败的 QQ 和错误信息。
- 默认 `matches` 中每条记录只保留以下字段：
  - `qq_number`
  - `qq_level`
  - `qq_nickname`
  - `qq_music_nickname`
  - `ip_address`
  - `qq_music_vip_level`

如果某个 QQ 的 `ip_address` 或 `qq_music_vip_level` 缺失，脚本会自动把当次真实返回的 QQ 音乐主页 HTML 和 SSR JSON 落到：

`output/debug_missing_music_fields/`

用于继续排查。

## QQ 等级查询

配置文件：`config/qq_level_query_config.json`

### 1. Cookie 配置

```json
"cookies": {
  "multiline": "",
  "items": [
    "uin=o000000001; skey=xxx; p_skey=xxx;",
    "uin=o000000002; skey=xxx; p_skey=xxx;"
  ]
}
```

- 每个 Cookie 至少需要包含 `uin/p_uin`、`skey`、`p_skey`。
- 查询时会按顺序轮询 Cookie。
- 如果本次请求失败，脚本会自动切到下一个 Cookie 重试。

### 2. 请求控制

```json
"request": {
  "retries": 2,
  "max_count": 0
}
```

- `retries`：单个 QQ 最多重试次数。
- `max_count`：本次最多处理数量，`0` 表示不限制。

### 3. 运行

```bash
python src/qq_level_query.py
```

输出结构：

- `summary.requested`：请求数量。
- `summary.succeeded`：成功数量。
- `summary.failed`：失败数量。
- `results`：成功结果。
- `failures`：失败记录。

## 建议

- 想降低风控：增加 Cookie、适当加大休眠、控制 `max_count` 分批执行。
- 想输出完整资料：把 `include_raw_profile_data` 设为 `true`，并把 `fields` 设为空数组。
- 如果 QQ 音乐字段缺失，优先看 `output/debug_missing_music_fields/` 里的真实返回页面，再决定怎么改提取逻辑。

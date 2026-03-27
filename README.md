# QQ 音乐资料抓取脚本使用说明

## 文件说明

- `qq_music_profile_fetcher.py`：原始脚本，不做修改。
- `qq_music_profile_fetcher_configurable.py`：优化后的新脚本。
- `qq_music_profile_fetcher_config.json`：统一配置文件。

## 功能概览

新脚本支持以下能力：

- Cookie 从配置文件读取，可配置多个 Cookie。
- 请求时按顺序轮询 Cookie，降低单个 Cookie 连续请求的风控压力。
- 可配置请求重试次数。
- 可配置每次请求后的休眠时间和随机抖动时间。
- 可直接写死一组 QQ 号，或通过“前缀 + 中间递增数字 + 后缀”批量生成。
- 可按字段筛选结果，例如 `ip_location`、`gender`、`fans_num` 等。
- 可配置最终输出字段，避免输出过多无关内容。

## 配置文件说明

配置文件路径：

`qq_music_profile_fetcher_config.json`

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
- 每个数组元素就是一整条 Cookie。
- 脚本会按顺序轮询使用这些 Cookie。

如果你想严格按“每行一个 Cookie”维护，可以这样写：

```json
"cookies": {
  "multiline": "uin=o000000001; qm_keyst=your_cookie_1;\nuin=o000000002; qm_keyst=your_cookie_2;",
  "items": []
}
```

`multiline` 和 `items` 会合并使用。

### 2. QQ 号直接配置

```json
"qq_numbers": [
  "12345678",
  "87654321"
]
```

- 适合少量固定 QQ 号。
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

以上示例会生成：

- `12000188`
- `12000288`
- `12000388`
- ...
- `12002088`

字段说明：

- `enabled`：是否启用批量生成。
- `prefix`：前缀。
- `suffix`：后缀。
- `pad_width`：中间数字补零位数。
- `start`：起始数字。
- `end`：结束数字。
- `step`：步长。

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

- `seconds`：每次请求后的固定休眠秒数。
- `jitter_seconds`：附加随机抖动秒数。

实际休眠时间为：

`seconds + 0 到 jitter_seconds 之间的随机值`

### 6. 字段筛选

```json
"filters": [
  {
    "field": "ip_location",
    "contains": "广东"
  }
]
```

支持的筛选方式：

- `equals`：完全匹配。
- `contains`：包含指定字符串。
- `not_contains`：不包含指定字符串。
- `regex`：正则匹配。
- `in`：在给定列表中。

可筛选字段示例：

- `qq_number`
- `name`
- `ip_location`
- `fans_num`
- `visitor_num`
- `gender`
- `constellation`
- `encrypt_uin`

示例：只保留 IP 属地包含“广东”的结果。

```json
"filters": [
  {
    "field": "ip_location",
    "contains": "广东"
  }
]
```

示例：只保留性别为“男”的结果。

```json
"filters": [
  {
    "field": "gender",
    "equals": "男"
  }
]
```

### 7. 输出配置

```json
"output": {
  "file": "qq_music_profile_results.json",
  "fields": [
    "qq_number",
    "name",
    "ip_location",
    "fans_num",
    "visitor_num",
    "gender",
    "used_cookie",
    "request_attempt"
  ],
  "include_raw_profile_data": false
}
```

- `file`：结果输出文件名。
- `fields`：输出时仅保留这些字段。
- `include_raw_profile_data`：是否保留完整原始资料。

## 运行方式

在当前目录执行：

```bash
python qq_music_profile_fetcher_configurable.py
```

运行后会：

- 控制台打印结果。
- 同时写入 `output.file` 指定的结果文件。

## 返回结果结构

输出 JSON 包含三部分：

- `summary`：统计信息。
- `matches`：筛选后的命中结果。
- `failures`：失败的 QQ 和错误信息。

## 常见调整建议

### 1. 想降低风控

- 增加 `cookies.items` 数量。
- 适当调大 `sleep.seconds`。
- 增加 `sleep.jitter_seconds`。
- 控制 `request.max_count`，分批跑。

### 2. 想抓全部结果，不做筛选

把 `filters` 改为空数组：

```json
"filters": []
```

### 3. 想输出完整资料

把：

```json
"include_raw_profile_data": true
```

同时建议 `fields` 留空数组：

```json
"fields": []
```

这样会输出完整字段。

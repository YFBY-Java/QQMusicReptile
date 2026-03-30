import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


API_URL = "https://api.icofun.cn/api/qq_level.php"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_FILE = CONFIG_DIR / "qq_level_query_config.json"


class CookieRotator:
    def __init__(self, cookies: List[str]):
        """缓存可用 Cookie，并提供顺序轮询能力。"""
        # 预先过滤空值，避免运行时每次都判断。
        self.cookies = [item.strip() for item in cookies if item and item.strip()]
        self.index = 0

    def next(self) -> str:
        """返回当前 Cookie，并把指针移动到下一个位置。"""
        # 查询失败时切换到下一个 Cookie，避免单个 Cookie 持续卡死整批任务。
        if not self.cookies:
            return ""
        cookie = self.cookies[self.index]
        self.index = (self.index + 1) % len(self.cookies)
        return cookie


def load_config(config_path: Path) -> Dict[str, Any]:
    """读取 JSON 配置文件。"""
    with config_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def parse_cookie_string(cookie: str) -> Dict[str, str]:
    """把原始 Cookie 字符串拆成键值对字典。"""
    cookie_map: Dict[str, str] = {}
    for part in cookie.split(";"):
        # 跳过空片段和不符合 key=value 形式的内容。
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        cookie_map[key.strip()] = value.strip()
    return cookie_map


def extract_auth_params(cookie: str) -> Dict[str, str]:
    """从 Cookie 中提取 QQ 等级接口所需的鉴权参数。"""
    cookie_map = parse_cookie_string(cookie)

    # 这个接口依赖 QQ 登录态里的关键鉴权字段，字段名在不同 Cookie 来源里可能略有差异。
    uin = cookie_map.get("uin") or cookie_map.get("p_uin") or cookie_map.get("o2_uin")
    skey = cookie_map.get("skey")
    pskey = cookie_map.get("p_skey") or cookie_map.get("pskey")

    missing = [name for name, value in {"uin": uin, "skey": skey, "pskey": pskey}.items() if not value]
    if missing:
        raise ValueError(f"Cookie 中缺少必要字段：{', '.join(missing)}")

    return {
        "uin": str(uin).strip(),
        "skey": str(skey).strip(),
        "pskey": str(pskey).strip(),
    }


def iter_cookies(config: Dict[str, Any]) -> CookieRotator:
    """从配置中加载 Cookie 列表，并构造成轮询器。"""
    cookie_config = config.get("cookies", {})
    # 同时兼容数组写法和多行文本写法，便于直接粘贴浏览器里的 Cookie。
    items = list(cookie_config.get("items", []))
    multiline = str(cookie_config.get("multiline", ""))
    if multiline.strip():
        items.extend(line.strip() for line in multiline.splitlines() if line.strip())
    return CookieRotator(items)


def deduplicate(values: Iterable[str]) -> List[str]:
    """按原始顺序去重。"""
    result: List[str] = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def build_qq_numbers(config: Dict[str, Any]) -> List[str]:
    """根据显式列表和生成器配置构造最终待查询 QQ 列表。"""
    explicit_numbers = [str(item).strip() for item in config.get("qq_numbers", []) if str(item).strip()]
    generator = config.get("qq_generator", {})

    if not generator.get("enabled", False):
        return deduplicate(explicit_numbers)

    # 生成规则固定为 prefix + 中间补零序号 + suffix。
    prefix = str(generator.get("prefix", ""))
    suffix = str(generator.get("suffix", ""))
    fill_width = int(generator.get("fill_width", generator.get("pad_width", 0)))
    max_value = (10 ** fill_width) - 1 if fill_width > 0 else 0
    start = int(generator.get("start", 0))
    end = int(generator.get("end", max_value))
    step = int(generator.get("step", 1))

    if fill_width < 0:
        raise ValueError("qq_generator.fill_width 不能小于 0")
    if step <= 0:
        raise ValueError("qq_generator.step 必须大于 0")
    if end < start:
        raise ValueError("qq_generator.end 不能小于 start")
    if end > max_value:
        raise ValueError("qq_generator.end 不能大于当前 fill_width 可表示的最大值")

    generated = [
        f"{prefix}{str(number).zfill(fill_width)}{suffix}"
        for number in range(start, end + 1, step)
    ]
    return deduplicate(explicit_numbers + generated)


def query_qq_level(target_qq: str, cookie: str) -> Dict[str, Any]:
    """使用单个 Cookie 查询一个 QQ 的等级资料。"""
    auth_params = extract_auth_params(cookie)
    # 该接口把目标 QQ 放在 query 参数里，其余鉴权信息全部来自 Cookie。
    params = {
        **auth_params,
        "qq": str(target_qq).strip(),
    }

    response = requests.get(API_URL, params=params, timeout=15)
    response.raise_for_status()

    try:
        result = json.loads(response.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # 即使接口偶发返回非 JSON，也保留原始文本方便排查。
        result = {"raw_text": response.text}

    return {
        "qq": str(target_qq).strip(),
        "result": result,
    }


def query_qq_level_with_retries(
    target_qq: str,
    cookie_rotator: CookieRotator,
    retries: int,
) -> Dict[str, Any]:
    """查询单个 QQ，并在失败时轮询切换 Cookie 重试。"""
    if not cookie_rotator.cookies:
        raise ValueError("配置文件中缺少有效 cookies")

    last_error: Optional[str] = None
    for attempt in range(1, retries + 1):
        # 每次重试都会轮到下一个 Cookie，避免单个 Cookie 连续失败。
        cookie = cookie_rotator.next()
        try:
            result = query_qq_level(target_qq, cookie)
            result["used_cookie"] = cookie
            result["request_attempt"] = attempt
            return result
        except Exception as exc:
            last_error = str(exc)

    raise RuntimeError(last_error or f"QQ {target_qq} 查询失败")


def main() -> None:
    """独立脚本入口：批量查询 QQ 等级并输出汇总结果。"""
    config = load_config(CONFIG_FILE)
    cookie_rotator = iter_cookies(config)
    if not cookie_rotator.cookies:
        raise ValueError("配置文件中缺少有效 cookies")

    qq_numbers = build_qq_numbers(config)
    if not qq_numbers:
        raise ValueError("没有可查询的 QQ 号，请检查 qq_numbers 或 qq_generator 配置")

    request_config = config.get("request", {})
    retries = max(1, int(request_config.get("retries", 1)))
    max_count = int(request_config.get("max_count", 0))

    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for index, qq_number in enumerate(qq_numbers, start=1):
        if max_count > 0 and index > max_count:
            break

        try:
            # 这里按 QQ 维度做重试；每次失败都会轮询到下一个 Cookie。
            results.append(query_qq_level_with_retries(qq_number, cookie_rotator, retries))
        except Exception as exc:
            # 失败记录保留下来，便于后续回头检查是 Cookie 问题还是目标 QQ 本身不可查。
            failures.append({"qq": qq_number, "error": str(exc)})

    # 独立脚本直接打印标准化汇总结构，便于人工查看或二次处理。
    print(
        json.dumps(
            {
                "summary": {
                    "requested": min(len(qq_numbers), max_count) if max_count > 0 else len(qq_numbers),
                    "succeeded": len(results),
                    "failed": len(failures),
                },
                "results": results,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

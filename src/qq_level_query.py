import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests


API_URL = "https://api.icofun.cn/api/qq_level.php"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_FILE = CONFIG_DIR / "qq_level_query_config.json"


def load_config(config_path: Path) -> Dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def parse_cookie_string(cookie: str) -> Dict[str, str]:
    cookie_map: Dict[str, str] = {}
    for part in cookie.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        cookie_map[key.strip()] = value.strip()
    return cookie_map


def extract_auth_params(cookie: str) -> Dict[str, str]:
    cookie_map = parse_cookie_string(cookie)

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


def deduplicate(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def build_qq_numbers(config: Dict[str, Any]) -> List[str]:
    explicit_numbers = [str(item).strip() for item in config.get("qq_numbers", []) if str(item).strip()]
    generator = config.get("qq_generator", {})

    if not generator.get("enabled", False):
        return deduplicate(explicit_numbers)

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
    auth_params = extract_auth_params(cookie)
    params = {
        **auth_params,
        "qq": str(target_qq).strip(),
    }

    response = requests.get(API_URL, params=params, timeout=15)
    response.raise_for_status()

    try:
        result = json.loads(response.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        result = {"raw_text": response.text}

    return {
        "qq": str(target_qq).strip(),
        "result": result,
    }


def main() -> None:
    config = load_config(CONFIG_FILE)
    cookie = str(config.get("cookie", "")).strip()
    if not cookie:
        raise ValueError("配置文件中缺少 cookie")

    qq_numbers = build_qq_numbers(config)
    if not qq_numbers:
        raise ValueError("没有可查询的 QQ 号，请检查 qq_numbers 或 qq_generator 配置")

    results = [query_qq_level(qq_number, cookie) for qq_number in qq_numbers]
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

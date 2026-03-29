import json
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


CONFIG_FILE = Path(__file__).with_name("qq_music_profile_fetcher_config.json")


class CookieRotator:
    def __init__(self, cookies: List[str]):
        self.cookies = [item.strip() for item in cookies if item and item.strip()]
        self.index = 0

    def next(self) -> str:
        if not self.cookies:
            return ""
        cookie = self.cookies[self.index]
        self.index = (self.index + 1) % len(self.cookies)
        return cookie


class QQMusicProfileClient:
    def __init__(self):
        self.session = requests.Session()
        self.api_headers = {
            "accept": "application/json",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "origin": "https://y.qq.com",
            "referer": "https://y.qq.com/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
            ),
        }
        self.page_headers = {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "cache-control": "max-age=0",
            "referer": "https://y.qq.com/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
            ),
        }

    @staticmethod
    def parse_json_response(resp: requests.Response) -> Dict[str, Any]:
        try:
            return json.loads(resp.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return resp.json()

    def get_created_diss_data(self, qq_number: str) -> Dict[str, Any]:
        url = "https://c6.y.qq.com/rsc/fcgi-bin/fcg_user_created_diss"
        params = {
            "r": "",
            "_": str(int(time.time() * 1000)),
            "cv": "",
            "ct": "",
            "format": "",
            "inCharset": "",
            "outCharset": "",
            "notice": "",
            "platform": "",
            "needNewCode": "",
            "uin": "",
            "g_tk_new_20200303": "",
            "g_tk": "",
            "hostuin": qq_number,
            "sin": "",
            "size": "50",
        }

        resp = self.session.get(url, headers=self.api_headers, params=params, timeout=15)
        resp.raise_for_status()

        data = self.parse_json_response(resp)
        if data.get("code") != 0:
            raise RuntimeError(f"获取用户歌单信息失败：{data}")

        created_diss_data = data.get("data", {})
        encrypt_uin = created_diss_data.get("encrypt_uin")
        if not encrypt_uin:
            raise RuntimeError(f"没有拿到 encrypt_uin，响应内容：{data}")
        return created_diss_data

    def get_profile_html(self, encrypt_uin: str, cookie: str) -> str:
        url = "https://i2.y.qq.com/n3/other/pages/share/profile_v2/index.html"
        params = {
            "userid": encrypt_uin,
            "ADTAG": "ryqq.profile",
            "redirect_from": "node_v2",
        }

        headers = dict(self.page_headers)
        if cookie:
            headers["Cookie"] = cookie

        resp = self.session.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def extract_ssr_data(html_text: str) -> Dict[str, Any]:
        pattern = r'window\.__ssrFirstPageData__\s*=\s*"((?:\\.|[^"\\])*)"'
        match = re.search(pattern, html_text, re.S)
        if not match:
            raise RuntimeError("没有在 HTML 中找到 window.__ssrFirstPageData__")

        escaped_json_str = match.group(1)
        json_str = json.loads(f'"{escaped_json_str}"')
        return json.loads(json_str)

    def get_profile_info(self, qq_number: str, cookie: str) -> Dict[str, Any]:
        created_diss_data = self.get_created_diss_data(qq_number)
        encrypt_uin = created_diss_data.get("encrypt_uin")
        html_text = self.get_profile_html(encrypt_uin, cookie)
        ssr_data = self.extract_ssr_data(html_text)

        info_root = ssr_data.get("homeData", {}).get("data", {}).get("Info", {})
        base_info = info_root.get("BaseInfo", {})
        playlists = created_diss_data.get("disslist", [])

        return {
            "qq_number": qq_number,
            "encrypt_uin": encrypt_uin,
            "name": base_info.get("Name"),
            "avatar": base_info.get("Avatar"),
            "big_avatar": base_info.get("BigAvatar"),
            "background_image": base_info.get("BackgroundImage"),
            "visitor_num": info_root.get("VisitorNum", {}).get("Num"),
            "fans_num": info_root.get("FansNum", {}).get("Num"),
            "follow_num": info_root.get("FollowNum", {}).get("Num"),
            "ip_location": info_root.get("IP", {}).get("Location"),
            "constellation": info_root.get("Constellation", {}).get("Constellation"),
            "gender": info_root.get("Gender", {}).get("Gender"),
            "playlist_hostname": created_diss_data.get("hostname"),
            "playlist_total": created_diss_data.get("totoal", len(playlists)),
            "playlists": playlists,
            "raw_profile_data": ssr_data,
        }


def load_config(config_path: Path) -> Dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def build_qq_numbers(config: Dict[str, Any]) -> List[str]:
    explicit_numbers = [str(item).strip() for item in config.get("qq_numbers", []) if str(item).strip()]
    generator = config.get("qq_generator", {})

    if not generator.get("enabled"):
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


def deduplicate(values: Iterable[str]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def get_nested_value(data: Dict[str, Any], field: str) -> Any:
    current: Any = data
    for part in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def normalize_filters(filters_config: Any) -> List[Dict[str, Any]]:
    if isinstance(filters_config, list):
        return [item for item in filters_config if isinstance(item, dict)]

    if not isinstance(filters_config, dict):
        return []

    normalized: List[Dict[str, Any]] = []
    for field, filter_item in filters_config.items():
        if not isinstance(filter_item, dict):
            continue
        if not filter_item.get("enabled", False):
            continue

        merged_item = dict(filter_item)
        merged_item.setdefault("field", field)
        normalized.append(merged_item)
    return normalized


def slugify_filename_part(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return "empty"

    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._ ")
    return text[:80] or "empty"


def build_filter_filename(filters: List[Dict[str, Any]], fallback_name: str) -> str:
    if not filters:
        return fallback_name

    suffix_parts: List[str] = []
    supported_operators = ("equals", "contains", "not_contains", "regex", "in")

    for filter_item in filters:
        field = slugify_filename_part(filter_item.get("field", "unknown"))
        operator = ""
        raw_value: Any = None

        for candidate in supported_operators:
            if candidate in filter_item and filter_item.get(candidate) is not None:
                operator = candidate
                raw_value = filter_item.get(candidate)
                break

        if isinstance(raw_value, list):
            value = "+".join(slugify_filename_part(item) for item in raw_value)
        else:
            value = slugify_filename_part(raw_value)

        operator = operator or "custom"
        suffix_parts.append(f"{field}-{operator}-{value}")

    base_name = Path(fallback_name).stem
    suffix = Path(fallback_name).suffix or ".json"
    return f"{base_name}__{'__'.join(suffix_parts)}{suffix}"


def is_match_filter(profile: Dict[str, Any], filters: List[Dict[str, Any]]) -> bool:
    for filter_item in filters:
        field = str(filter_item.get("field", "")).strip()
        if not field:
            continue

        actual_value = get_nested_value(profile, field)
        actual_text = "" if actual_value is None else str(actual_value)

        equals = filter_item.get("equals")
        if equals is not None and actual_text != str(equals):
            return False

        contains = filter_item.get("contains")
        if contains is not None and str(contains) not in actual_text:
            return False

        not_contains = filter_item.get("not_contains")
        if not_contains is not None and str(not_contains) in actual_text:
            return False

        regex = filter_item.get("regex")
        if regex and not re.search(str(regex), actual_text):
            return False

        in_list = filter_item.get("in")
        if in_list is not None:
            accepted = {str(item) for item in in_list}
            if actual_text not in accepted:
                return False

    return True


def select_output_fields(profile: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    if not fields:
        return profile

    result: Dict[str, Any] = {}
    for field in fields:
        result[field] = get_nested_value(profile, field)
    return result


def sleep_with_jitter(sleep_config: Dict[str, Any]) -> None:
    base_seconds = float(sleep_config.get("seconds", 0))
    jitter_seconds = float(sleep_config.get("jitter_seconds", 0))
    delay = base_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0, jitter_seconds)

    if delay > 0:
        time.sleep(delay)


def iter_cookies(config: Dict[str, Any]) -> CookieRotator:
    cookie_config = config.get("cookies", {})
    items = list(cookie_config.get("items", []))
    multiline = str(cookie_config.get("multiline", ""))
    if multiline.strip():
        items.extend(line.strip() for line in multiline.splitlines() if line.strip())
    return CookieRotator(items)


def process_profiles(config: Dict[str, Any]) -> Dict[str, Any]:
    qq_numbers = build_qq_numbers(config)
    if not qq_numbers:
        raise ValueError("没有可处理的 QQ 号，请检查 qq_numbers 或 qq_generator 配置")

    output_config = config.get("output", {})
    filters = normalize_filters(config.get("filters", []))
    include_raw = bool(output_config.get("include_raw_profile_data", False))
    selected_fields = output_config.get("fields", [])
    max_count = int(config.get("request", {}).get("max_count", 0))
    request_retries = int(config.get("request", {}).get("retries", 1))

    client = QQMusicProfileClient()
    cookie_rotator = iter_cookies(config)

    matches: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for index, qq_number in enumerate(qq_numbers, start=1):
        if max_count > 0 and index > max_count:
            break

        last_error: Optional[str] = None
        success = False
        for attempt in range(1, request_retries + 1):
            cookie = cookie_rotator.next()
            try:
                profile = client.get_profile_info(qq_number, cookie)
                profile["used_cookie"] = cookie
                profile["request_attempt"] = attempt
                success = True

                if not include_raw:
                    profile.pop("raw_profile_data", None)

                if is_match_filter(profile, filters):
                    matches.append(select_output_fields(profile, selected_fields))
                break
            except Exception as exc:
                last_error = str(exc)

        if not success and last_error:
            failures.append({"qq_number": qq_number, "error": last_error})

        sleep_with_jitter(config.get("sleep", {}))

    return {
        "summary": {
            "requested": min(len(qq_numbers), max_count) if max_count > 0 else len(qq_numbers),
            "matched": len(matches),
            "failed": len(failures),
        },
        "matches": matches,
        "failures": failures,
    }


def save_output(config: Dict[str, Any], data: Dict[str, Any]) -> None:
    output_config = config.get("output", {})
    output_dir = Path(output_config.get("directory", "output"))
    output_file = str(output_config.get("file", "qq_music_profile_results.json")).strip() or "qq_music_profile_results.json"
    output_encoding = str(output_config.get("encoding", "utf-8")).strip() or "utf-8"
    normalized_filters = normalize_filters(config.get("filters", []))

    if not output_dir.is_absolute():
        output_dir = CONFIG_FILE.parent / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = Path(output_file)
    if output_path.is_absolute():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        dynamic_name = build_filter_filename(normalized_filters, output_path.name)
        output_path = output_path.parent / dynamic_name
    else:
        dynamic_name = build_filter_filename(normalized_filters, output_path.name)
        output_path = output_dir / dynamic_name

    with output_path.open("w", encoding=output_encoding) as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def main() -> None:
    config = load_config(CONFIG_FILE)
    result = process_profiles(config)
    save_output(config, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

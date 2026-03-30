import json
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from qq_level_query import (
    API_URL as QQ_LEVEL_API_URL,
    CONFIG_FILE as QQ_LEVEL_CONFIG_FILE,
    extract_auth_params,
    load_config as load_qq_level_config,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "qq_music_profile_fetcher_config.json"
VIP_LEVEL_PATTERN = re.compile(r"(?:^|[\\/_-])(?:s?vip)(\d+)(?:\D|$)", re.IGNORECASE)


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
    def extract_qq_music_vip_level(info_root: Dict[str, Any]) -> Optional[int]:
        candidates: List[str] = []

        new_icon_info = info_root.get("NewIconInfo", {})
        for item in new_icon_info.get("iconlist", []) or []:
            if not isinstance(item, dict):
                continue

            ext = str(item.get("ext", ""))
            tips = str(item.get("Tips", ""))
            help_txt = str(item.get("Helptxt", ""))
            is_music_vip = ("tab1=svip" in ext) or ("绿钻" in tips) or ("绿钻" in help_txt)
            if not is_music_vip:
                continue

            for key in ("srcUrl", "GifURL", "GreyURL"):
                value = str(item.get(key, "")).strip()
                if value:
                    candidates.append(value)

        for item in info_root.get("Icons", []) or []:
            if not isinstance(item, dict):
                continue
            value = str(item.get("IconURL", "")).strip()
            if value:
                candidates.append(value)

        levels: List[int] = []
        for text in candidates:
            match = VIP_LEVEL_PATTERN.search(text)
            if match:
                levels.append(int(match.group(1)))

        return max(levels) if levels else None

    @staticmethod
    def find_nested_dict_by_key(data: Any, target_key: str) -> Dict[str, Any]:
        if isinstance(data, dict):
            value = data.get(target_key)
            if isinstance(value, dict):
                return value
            for item in data.values():
                result = QQMusicProfileClient.find_nested_dict_by_key(item, target_key)
                if result:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = QQMusicProfileClient.find_nested_dict_by_key(item, target_key)
                if result:
                    return result
        return {}

    @staticmethod
    def extract_ip_location_from_html(html_text: str) -> Optional[str]:
        patterns = [
            r'"IP":\{"Location":"([^"]*)"\}',
            r'personal__info_txt[^>]*>\s*[^<]*?\|\s*[^<]*?\|\s*([^<|]+?)\s*</',
            r'personal__info_txt[^>]*>\s*([^<]*?)\s*</',
            r'"Location":"([^"]*)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text)
            if match:
                value = match.group(1).strip()
                if "|" in value:
                    parts = [part.strip() for part in value.split("|") if part.strip()]
                    if parts:
                        return parts[-1]
                if value:
                    return value
        return None

    @staticmethod
    def extract_vip_level_from_html(html_text: str) -> Optional[int]:
        levels = [int(item) for item in re.findall(r'(?:^|[\\/_-])(?:s?vip)(\d+)(?:\D|$)', html_text, re.IGNORECASE)]

        vip_img_match = re.search(
            r'personal__ident_img[^>]+src="[^"]*(?:s?vip)(\d+)\.(?:png|webp)"[^>]*alt="[^"]*(?:绿钻|VIP)[^"]*"',
            html_text,
            re.IGNORECASE,
        )
        if vip_img_match:
            levels.append(int(vip_img_match.group(1)))

        return max(levels) if levels else None

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
        if not info_root:
            info_root = self.find_nested_dict_by_key(ssr_data, "Info")
        base_info = info_root.get("BaseInfo", {})
        playlists = created_diss_data.get("disslist", [])
        vip_level = self.extract_qq_music_vip_level(info_root)
        if vip_level is None:
            vip_level = self.extract_vip_level_from_html(html_text)
        ip_location = info_root.get("IP", {}).get("Location")
        if not ip_location:
            ip_location = self.extract_ip_location_from_html(html_text)
        qq_music_nickname = base_info.get("Name") or created_diss_data.get("hostname")

        return {
            "qq_number": qq_number,
            "qq_music_nickname": qq_music_nickname,
            "encrypt_uin": encrypt_uin,
            "name": qq_music_nickname,
            "avatar": base_info.get("Avatar"),
            "big_avatar": base_info.get("BigAvatar"),
            "background_image": base_info.get("BackgroundImage"),
            "visitor_num": info_root.get("VisitorNum", {}).get("Num"),
            "fans_num": info_root.get("FansNum", {}).get("Num"),
            "follow_num": info_root.get("FollowNum", {}).get("Num"),
            "ip_location": ip_location,
            "constellation": info_root.get("Constellation", {}).get("Constellation"),
            "gender": info_root.get("Gender", {}).get("Gender"),
            "qq_music_vip_level": vip_level,
            "playlist_hostname": created_diss_data.get("hostname"),
            "playlist_total": created_diss_data.get("totoal", len(playlists)),
            "playlists": playlists,
            "raw_profile_data": ssr_data,
            "raw_profile_html": html_text,
        }


class QQLevelClient:
    def __init__(self, cookie: str):
        self.cookie = str(cookie).strip()
        try:
            self.auth_params = extract_auth_params(self.cookie) if self.cookie else {}
        except Exception:
            self.auth_params = {}

    @property
    def enabled(self) -> bool:
        return bool(self.auth_params)

    def query(self, qq_number: str) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        try:
            response = requests.get(
                QQ_LEVEL_API_URL,
                params={**self.auth_params, "qq": str(qq_number).strip()},
                timeout=15,
            )
            response.raise_for_status()

            try:
                return json.loads(response.content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {"raw_text": response.text}
        except Exception:
            return {}


def load_optional_qq_level_cookie() -> str:
    if not QQ_LEVEL_CONFIG_FILE.exists():
        return ""

    try:
        config = load_qq_level_config(QQ_LEVEL_CONFIG_FILE)
    except Exception:
        return ""
    return str(config.get("cookie", "")).strip()


def find_first_value(data: Any, candidate_keys: List[str]) -> Any:
    normalized_keys = {key.lower() for key in candidate_keys}

    def _search(value: Any) -> Any:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in normalized_keys and item not in (None, ""):
                    return item
            for item in value.values():
                result = _search(item)
                if result not in (None, ""):
                    return result
        elif isinstance(value, list):
            for item in value:
                result = _search(item)
                if result not in (None, ""):
                    return result
        return None

    return _search(data)


def normalize_qq_level_profile(level_data: Dict[str, Any]) -> Dict[str, Any]:
    qq_nickname = find_first_value(
        level_data,
        [
            "nickname",
            "nick_name",
            "nick",
            "qqnickname",
            "qq_nickname",
            "qqnick",
            "name",
        ],
    )
    qq_level = find_first_value(
        level_data,
        [
            "level",
            "qqlevel",
            "qq_level",
            "grade",
            "iqqlevel",
            "levelnum",
            "level_num",
        ],
    )

    return {
        "qq_nickname": qq_nickname,
        "qq_level": qq_level,
        "qq_level_raw": level_data,
    }


def save_debug_profile_payload(
    qq_number: str,
    html_text: str,
    ssr_data: Dict[str, Any],
    output_dir: Path,
) -> None:
    debug_dir = output_dir / "debug_missing_music_fields"
    debug_dir.mkdir(parents=True, exist_ok=True)

    html_path = debug_dir / f"{qq_number}.html"
    json_path = debug_dir / f"{qq_number}.json"

    html_path.write_text(html_text, encoding="utf-8")
    json_path.write_text(json.dumps(ssr_data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    output_dir = Path(output_config.get("directory", "output"))
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    filters = normalize_filters(config.get("filters", []))
    include_raw = bool(output_config.get("include_raw_profile_data", False))
    selected_fields = output_config.get("fields", [])
    max_count = int(config.get("request", {}).get("max_count", 0))
    request_retries = int(config.get("request", {}).get("retries", 1))

    client = QQMusicProfileClient()
    qq_level_client = QQLevelClient(load_optional_qq_level_cookie())
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
                profile.update(normalize_qq_level_profile(qq_level_client.query(qq_number)))
                profile["ip_address"] = profile.get("ip_location")
                profile["used_cookie"] = cookie
                profile["request_attempt"] = attempt

                if profile.get("ip_address") in (None, "") or profile.get("qq_music_vip_level") is None:
                    save_debug_profile_payload(
                        qq_number=qq_number,
                        html_text=str(profile.get("raw_profile_html", "")),
                        ssr_data=profile.get("raw_profile_data", {}),
                        output_dir=output_dir,
                    )
                success = True

                if not include_raw:
                    profile.pop("raw_profile_data", None)
                    profile.pop("raw_profile_html", None)
                    profile.pop("qq_level_raw", None)

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
        output_dir = PROJECT_ROOT / output_dir
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

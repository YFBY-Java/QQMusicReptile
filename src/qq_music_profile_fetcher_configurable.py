import json
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from qq_level_query import (
    CONFIG_FILE as QQ_LEVEL_CONFIG_FILE,
    iter_cookies as iter_qq_level_cookies,
    load_config as load_qq_level_config,
    query_qq_level_with_retries,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "qq_music_profile_fetcher_config.json"
VIP_LEVEL_PATTERN = re.compile(r"(?:^|[\\/_-])(?:s?vip)(\d+)(?:\D|$)", re.IGNORECASE)


class CookieRotator:
    def __init__(self, cookies: List[str]):
        """缓存可用 Cookie，并提供顺序轮询能力。"""
        # 预先过滤空值，避免运行时每次都判断。
        self.cookies = [item.strip() for item in cookies if item and item.strip()]
        self.index = 0

    def next(self) -> str:
        """返回当前 Cookie，并把指针移动到下一个位置。"""
        if not self.cookies:
            return ""
        cookie = self.cookies[self.index]
        self.index = (self.index + 1) % len(self.cookies)
        return cookie


class QQMusicProfileClient:
    def __init__(self):
        """初始化 QQ 音乐资料抓取会话和默认请求头。"""
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
        """优先按 UTF-8 解析响应 JSON，失败后回退到 requests 自带解析。"""
        try:
            return json.loads(resp.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return resp.json()

    def get_created_diss_data(self, qq_number: str) -> Dict[str, Any]:
        """查询用户歌单列表，并提取后续主页请求需要的 encrypt_uin。"""
        url = "https://c6.y.qq.com/rsc/fcgi-bin/fcg_user_created_diss"
        # 这条接口的关键输入是 hostuin，目标 QQ 号从这里进入查询链路。
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

        # encrypt_uin 是后续访问 QQ 音乐主页分享页的关键标识。
        created_diss_data = data.get("data", {})
        encrypt_uin = created_diss_data.get("encrypt_uin")
        if not encrypt_uin:
            raise RuntimeError(f"没有拿到 encrypt_uin，响应内容：{data}")
        return created_diss_data

    def get_profile_html(self, encrypt_uin: str, cookie: str) -> str:
        """使用 encrypt_uin 和 Cookie 请求 QQ 音乐分享主页 HTML。"""
        url = "https://i2.y.qq.com/n3/other/pages/share/profile_v2/index.html"
        params = {
            "userid": encrypt_uin,
            "ADTAG": "ryqq.profile",
            "redirect_from": "node_v2",
        }

        headers = dict(self.page_headers)
        if cookie:
            # 按你的实际使用方式，主页请求必须带 Cookie。
            headers["Cookie"] = cookie

        resp = self.session.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def extract_qq_music_vip_level(info_root: Dict[str, Any]) -> Optional[int]:
        """从 SSR 的图标信息中解析 QQ 音乐 VIP 等级。"""
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

        # 老页面和新页面的 VIP 图标入口不完全一致，所以两个入口都扫一遍。
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
        """递归查找指定键对应的首个字典值，用于兼容页面结构漂移。"""
        if isinstance(data, dict):
            # 先检查当前层，再向下递归，尽量优先拿到距离根更近的目标结构。
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
        """当 SSR 取不到 IP 时，直接从 HTML 文本中兜底提取属地。"""
        patterns = [
            r'"IP":\{"Location":"([^"]*)"\}',
            r'personal__info_txt[^>]*>\s*[^<]*?\|\s*[^<]*?\|\s*([^<|]+?)\s*</',
            r'personal__info_txt[^>]*>\s*([^<]*?)\s*</',
            r'"Location":"([^"]*)"',
        ]
        for pattern in patterns:
            # 先匹配结构化 JSON，再匹配页面可见文本，最后退化到宽泛 Location 搜索。
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
        """当 SSR 取不到 VIP 时，直接从 HTML 中的图标地址兜底提取等级。"""
        # 页面里常见的是 vip6/svip6 这种图片名，直接从全量 HTML 提取最大等级。
        levels = [int(item) for item in re.findall(r'(?:^|[\\/_-])(?:s?vip)(\d+)(?:\D|$)', html_text, re.IGNORECASE)]

        vip_img_match = re.search(
            r'personal__ident_img[^>]+src="[^"]*(?:s?vip)(\d+)\.(?:png|webp)"[^>]*alt="[^"]*(?:绿钻|VIP)[^"]*"',
            html_text,
            re.IGNORECASE,
        )
        if vip_img_match:
            # 如果页面显式渲染了 VIP 图标，把它也加入候选，和全量搜索结果取最大值。
            levels.append(int(vip_img_match.group(1)))

        return max(levels) if levels else None

    @staticmethod
    def extract_ssr_data(html_text: str) -> Dict[str, Any]:
        """从分享页 HTML 中抽取并反序列化首屏 SSR 数据。"""
        # QQ 音乐分享页把首屏数据放在一个转义后的 JSON 字符串里，需要先反转义再反序列化。
        pattern = r'window\.__ssrFirstPageData__\s*=\s*"((?:\\.|[^"\\])*)"'
        match = re.search(pattern, html_text, re.S)
        if not match:
            raise RuntimeError("没有在 HTML 中找到 window.__ssrFirstPageData__")

        escaped_json_str = match.group(1)
        json_str = json.loads(f'"{escaped_json_str}"')
        return json.loads(json_str)

    def get_profile_info(self, qq_number: str, cookie: str) -> Dict[str, Any]:
        """整合歌单接口和主页接口，返回单个 QQ 的完整音乐资料。"""
        created_diss_data = self.get_created_diss_data(qq_number)
        encrypt_uin = created_diss_data.get("encrypt_uin")
        html_text = self.get_profile_html(encrypt_uin, cookie)
        ssr_data = self.extract_ssr_data(html_text)

        # 正常情况直接走首选路径；如果页面结构调整，再退化到递归查找 Info。
        info_root = ssr_data.get("homeData", {}).get("data", {}).get("Info", {})
        if not info_root:
            info_root = self.find_nested_dict_by_key(ssr_data, "Info")
        base_info = info_root.get("BaseInfo", {})
        playlists = created_diss_data.get("disslist", [])
        vip_level = self.extract_qq_music_vip_level(info_root)
        if vip_level is None:
            # SSR 结构取不到时，再从页面里直接扫 vip 图标地址。
            vip_level = self.extract_vip_level_from_html(html_text)
        ip_location = info_root.get("IP", {}).get("Location")
        if not ip_location:
            # 带 Cookie 返回的页面可能被裁剪，这里退回到页面可见文本兜底。
            ip_location = self.extract_ip_location_from_html(html_text)
        # 昵称优先取主页名字；如果主页字段为空，再退到歌单接口的 hostname。
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
    def __init__(self, config: Dict[str, Any]):
        """初始化 QQ 等级查询客户端，并复用等级模块的 Cookie 轮询策略。"""
        # 主脚本直接复用 QQ 等级模块的 Cookie 轮询规则，避免两套配置行为不一致。
        self.cookie_rotator = iter_qq_level_cookies(config)
        self.retries = max(1, int(config.get("request", {}).get("retries", 1)))
        try:
            self.enabled_value = bool(self.cookie_rotator.cookies)
        except Exception:
            self.enabled_value = False

    @property
    def enabled(self) -> bool:
        """标记当前是否具备可用的 QQ 等级查询 Cookie。"""
        return self.enabled_value

    def query(self, qq_number: str) -> Dict[str, Any]:
        """查询单个 QQ 的等级资料；失败时返回空字典而不是中断主流程。"""
        if not self.enabled:
            return {}
        try:
            # 主流程里 QQ 等级只是附加信息，不应该因为它失败就影响 QQ 音乐资料输出。
            return query_qq_level_with_retries(qq_number, self.cookie_rotator, self.retries)
        except Exception:
            return {}


def load_optional_qq_level_config() -> Dict[str, Any]:
    """尝试读取 QQ 等级配置；不存在或解析失败时返回空配置。"""
    if not QQ_LEVEL_CONFIG_FILE.exists():
        return {}

    try:
        return load_qq_level_config(QQ_LEVEL_CONFIG_FILE)
    except Exception:
        return {}


def find_first_value(data: Any, candidate_keys: List[str]) -> Any:
    """递归查找候选键中的首个非空值。"""
    normalized_keys = {key.lower() for key in candidate_keys}

    def _search(value: Any) -> Any:
        if isinstance(value, dict):
            # 先命中当前层键名，再递归子层，优先使用离根更近的语义字段。
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
    """把 QQ 等级接口的不稳定字段名归一成主脚本使用的固定字段。"""
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
        # QQ 等级接口字段不稳定，这里统一归一到主结果需要的字段名。
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
    """当音乐字段缺失时，把真实页面和 SSR 数据落盘用于排查。"""
    # 音乐字段缺失时，把真实响应落盘，后续可以直接对照页面结构修提取逻辑。
    debug_dir = output_dir / "debug_missing_music_fields"
    debug_dir.mkdir(parents=True, exist_ok=True)

    html_path = debug_dir / f"{qq_number}.html"
    json_path = debug_dir / f"{qq_number}.json"

    html_path.write_text(html_text, encoding="utf-8")
    json_path.write_text(json.dumps(ssr_data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(config_path: Path) -> Dict[str, Any]:
    """读取 JSON 配置文件。"""
    with config_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def build_qq_numbers(config: Dict[str, Any]) -> List[str]:
    """根据显式列表和生成器配置构造最终待抓取 QQ 列表。"""
    explicit_numbers = [str(item).strip() for item in config.get("qq_numbers", []) if str(item).strip()]
    generator = config.get("qq_generator", {})

    if not generator.get("enabled"):
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


def deduplicate(values: Iterable[str]) -> List[str]:
    """按原始顺序去重。"""
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def get_nested_value(data: Dict[str, Any], field: str) -> Any:
    """按 a.b.c 形式的路径从字典中取值。"""
    current: Any = data
    for part in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def normalize_filters(filters_config: Any) -> List[Dict[str, Any]]:
    """把字典或数组形式的筛选配置统一归一成列表结构。"""
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

        # 字典写法里字段名来自外层 key，这里补到统一结构里，后续处理就不用区分来源。
        merged_item = dict(filter_item)
        merged_item.setdefault("field", field)
        normalized.append(merged_item)
    return normalized


def slugify_filename_part(value: Any) -> str:
    """把任意值清洗成适合拼接到文件名里的安全片段。"""
    text = "" if value is None else str(value).strip()
    if not text:
        return "empty"

    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._ ")
    return text[:80] or "empty"


def build_filter_filename(filters: List[Dict[str, Any]], fallback_name: str) -> str:
    """根据当前筛选条件动态生成输出文件名。"""
    if not filters:
        return fallback_name

    suffix_parts: List[str] = []
    supported_operators = ("equals", "contains", "not_contains", "regex", "in")

    for filter_item in filters:
        field = slugify_filename_part(filter_item.get("field", "unknown"))
        operator = ""
        raw_value: Any = None

        # 只取当前筛选项里第一个生效的操作符，用来生成稳定文件名。
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
    """判断单个资料是否满足全部筛选条件。"""
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
    """按配置挑选最终输出字段；为空时返回完整资料。"""
    if not fields:
        return profile

    result: Dict[str, Any] = {}
    for field in fields:
        result[field] = get_nested_value(profile, field)
    return result


def sleep_with_jitter(sleep_config: Dict[str, Any]) -> None:
    """按照基础间隔加随机抖动进行休眠，降低连续请求节奏。"""
    base_seconds = float(sleep_config.get("seconds", 0))
    jitter_seconds = float(sleep_config.get("jitter_seconds", 0))
    delay = base_seconds
    if jitter_seconds > 0:
        # 抖动用于让请求间隔不要完全固定，减少规律性。
        delay += random.uniform(0, jitter_seconds)

    if delay > 0:
        time.sleep(delay)


def iter_cookies(config: Dict[str, Any]) -> CookieRotator:
    """从主配置中加载 QQ 音乐 Cookie 列表，并构造成轮询器。"""
    cookie_config = config.get("cookies", {})
    # 同时兼容数组写法和多行文本写法，便于直接粘贴浏览器里的 Cookie。
    items = list(cookie_config.get("items", []))
    multiline = str(cookie_config.get("multiline", ""))
    if multiline.strip():
        items.extend(line.strip() for line in multiline.splitlines() if line.strip())
    return CookieRotator(items)


def process_profiles(config: Dict[str, Any]) -> Dict[str, Any]:
    """主处理流程：批量抓取、补充 QQ 等级、筛选并汇总结果。"""
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
    qq_level_client = QQLevelClient(load_optional_qq_level_config())
    cookie_rotator = iter_cookies(config)

    matches: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for index, qq_number in enumerate(qq_numbers, start=1):
        if max_count > 0 and index > max_count:
            break

        last_error: Optional[str] = None
        success = False
        for attempt in range(1, request_retries + 1):
            # QQ 音乐主链路也按次轮询 Cookie，失败时自动换 Cookie 继续。
            cookie = cookie_rotator.next()
            try:
                profile = client.get_profile_info(qq_number, cookie)
                profile.update(normalize_qq_level_profile(qq_level_client.query(qq_number)))
                # 最终输出字段统一收敛到业务侧约定的名字。
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
                    # 默认输出精简结果；调试时可以通过配置保留原始响应。
                    profile.pop("raw_profile_data", None)
                    profile.pop("raw_profile_html", None)
                    profile.pop("qq_level_raw", None)

                if is_match_filter(profile, filters):
                    # fields 留空时输出完整结构；否则只输出配置里声明的字段。
                    matches.append(select_output_fields(profile, selected_fields))
                break
            except Exception as exc:
                # 只记录最后一次错误，避免失败列表里塞进重复噪音。
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
    """把结果按配置写入目标目录和文件。"""
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
        # 相对路径统一落到 output.directory 下，并根据筛选条件动态追加文件名后缀。
        dynamic_name = build_filter_filename(normalized_filters, output_path.name)
        output_path = output_dir / dynamic_name

    with output_path.open("w", encoding=output_encoding) as fp:
        # 使用缩进 JSON 便于直接人工阅读和继续排查。
        json.dump(data, fp, ensure_ascii=False, indent=2)


def main() -> None:
    """主脚本入口：读取配置、执行抓取并保存结果。"""
    config = load_config(CONFIG_FILE)
    result = process_profiles(config)
    save_output(config, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

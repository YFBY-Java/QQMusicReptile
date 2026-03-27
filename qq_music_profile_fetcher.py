import json
import re
import time
from typing import Any, Dict

import requests


# =========================
# 这里直接填写
# =========================
QQ_NUMBER = ""
COOKIE = ""





class QQMusicProfileClient:
    def __init__(self, cookie: str = ""):
        self.session = requests.Session()
        self.cookie = cookie

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
        if self.cookie:
            self.page_headers["Cookie"] = self.cookie

    def get_encrypt_uin(self, qq_number: str) -> str:
        """
        根据 QQ 号获取 encrypt_uin
        """
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

        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取 encrypt_uin 失败：{data}")

        encrypt_uin = data.get("data", {}).get("encrypt_uin")
        if not encrypt_uin:
            raise RuntimeError(f"没有拿到 encrypt_uin，响应内容：{data}")

        return encrypt_uin

    def get_profile_html(self, encrypt_uin: str) -> str:
        """
        根据 encrypt_uin 获取主页 HTML
        """
        url = "https://i2.y.qq.com/n3/other/pages/share/profile_v2/index.html"
        params = {
            "userid": encrypt_uin,
            "ADTAG": "ryqq.profile",
            "redirect_from": "node_v2",
        }

        resp = self.session.get(url, headers=self.page_headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def extract_ssr_data(html_text: str) -> Dict[str, Any]:
        """
        从 HTML 中提取 window.__ssrFirstPageData__
        """
        pattern = r'window\.__ssrFirstPageData__\s*=\s*"((?:\\.|[^"\\])*)"'
        match = re.search(pattern, html_text, re.S)
        if not match:
            raise RuntimeError("没有在 HTML 中找到 window.__ssrFirstPageData__")

        escaped_json_str = match.group(1)

        # 先把 JS 字符串还原，再转成 JSON 对象
        json_str = json.loads(f'"{escaped_json_str}"')
        return json.loads(json_str)

    def get_profile_info(self, qq_number: str) -> Dict[str, Any]:
        encrypt_uin = self.get_encrypt_uin(qq_number)
        html_text = self.get_profile_html(encrypt_uin)
        ssr_data = self.extract_ssr_data(html_text)

        info_root = ssr_data.get("homeData", {}).get("data", {}).get("Info", {})
        base_info = info_root.get("BaseInfo", {})

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
            "raw_profile_data": ssr_data,
        }


def main():
    client = QQMusicProfileClient(cookie=COOKIE)

    try:
        profile = client.get_profile_info(QQ_NUMBER)
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"执行失败：{e}")


if __name__ == "__main__":
    main()
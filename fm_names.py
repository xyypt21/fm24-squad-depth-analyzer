"""球员英文全名 -> 中文翻译。

全部用 deep-translator 在线翻译（Google 翻译，经本地代理）并发完成。
翻译仅在当前进程内存缓存，不写回任何文件。
"""

import os
import socket
from concurrent.futures import ThreadPoolExecutor

# 进程内在线翻译缓存与失败黑名单（避免重复请求）
_cache: dict = {}
_failed: set = set()

# 本地代理（Clash 等默认端口 7897），走境外 Google 翻译必需。
# 可通过环境变量 FM_PROXY 覆盖，空字符串/None 表示不走代理。
PROXY_URL = os.environ.get("FM_PROXY", "http://127.0.0.1:7897")

# 单次请求超时（deep-translator 未显式设 timeout，代理不通会无限阻塞）
_REQUEST_TIMEOUT = 10
# 并发翻译线程数
_MAX_WORKERS = 4
# 单个名字失败重试次数
_RETRIES = 2


def _proxies():
    if not PROXY_URL:
        return None
    return {"http": PROXY_URL, "https": PROXY_URL}


def _translate_one(name, translator):
    """翻译单个名字（失败重试）；成功写内存缓存，最终失败加入黑名单。"""
    for _ in range(_RETRIES):
        try:
            cn = translator.translate(name)
            cn = (cn or "").strip()
            if cn and cn != name:
                _cache[name] = cn
                return name, cn
        except Exception:
            continue
    _failed.add(name)
    return name, None


def _batch_translate(names):
    """批量翻译一批名字：并发走 Google 翻译（经本地代理）。

    返回 {name: 中文}；翻译异常/未命中的名字不返回（由调用方原样保留）。
    只走内存缓存，不写文件。
    """
    todo = []
    result = {}
    for n in names:
        if n in _cache:
            result[n] = _cache[n]
        elif n not in _failed:
            todo.append(n)
    if not todo:
        return result

    try:
        from deep_translator import GoogleTranslator

        # deep-translator 未设 socket 超时，代理不通会无限阻塞；设全局默认超时兜底
        _previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(_REQUEST_TIMEOUT)
        try:
            # 复用同一个 translator（requests 内部复用连接），并发翻译
            translator = GoogleTranslator(source="auto", target="zh-CN", proxies=_proxies())
            with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
                futs = {pool.submit(_translate_one, n, translator) for n in todo}
                for fut in futs:
                    name, cn = fut.result()
                    if cn:
                        result[name] = cn
        finally:
            socket.setdefaulttimeout(_previous)
    except ImportError:
        pass
    return result


def auto_translate(names):
    """翻译一批英文名，全部走 Google 在线翻译（经本地代理），并发执行。"""
    names = tuple(dict.fromkeys(names))
    if not names:
        return {}
    return _batch_translate(names)

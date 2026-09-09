"""阶段 5 T47–T50：``web.py``（前端资产 + cmd_web 增量端点）。"""

from __future__ import annotations

import re

from flowing.interfaces import EXIT_OK
from flowing.interfaces import web as web_mod
from flowing.interfaces.web import (
    WEB_EXTRA_ENDPOINTS,
    FrontendAssets,
    cmd_web,
    get_frontend_assets,
)
from flowing.message import MessagePriority

from .support import start_http, stop_http


def test_t47_assets_cache_stable():
    """get_frontend_assets() 调两次：index_html 与 assets 键集相同。"""
    first = get_frontend_assets()
    second = get_frontend_assets()
    assert first.index_html == second.index_html
    assert set(first.assets) == set(second.assets)
    assert first is second   # 允许返回同一缓存实例


def test_t48_singlefile_asset_pack():
    """单文件形态（webui-dist 构建产物全内联）：index_html 非空、assets 表
    为空（/assets/* 一律 404）、无 /assets/ 引用残留。"""
    assets = get_frontend_assets()
    assert isinstance(assets, FrontendAssets)
    assert assets.index_html.strip()
    assert assets.assets == {}
    assert not re.findall(r"/assets/([\w./-]+)", assets.index_html)


def test_t48b_frontend_contract_locked():
    """文本级锁：构建产物须消费 serve 的 SSE（stream / thinking /
    message_id 归位）、tree/rewind、models/model、status（上下文进度条）
    （无浏览器下的契约性兜底；防前端与端点面漂移）。"""
    html = get_frontend_assets().index_html
    for marker in ("stream", "thinking", "message_id", "tree", "rewind",
                   "model", "status"):
        assert marker in html, marker


async def test_t49_web_index_and_assets(project_ok, persist_dir, monkeypatch):
    """GET / → 200 text/html（单文件资产）；未命中 /assets/* → 404 不报错
    退出。"""
    handle = await start_http(monkeypatch, web_mod, cmd_web, project_ok, persist_dir)
    try:
        r = await handle.client.get("/")
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/html")
        assert r.text == get_frontend_assets().index_html

        assert not re.findall(r"/assets/([\w./-]+)", r.text)   # 单文件形态无外链资产
        assert (await handle.client.get("/assets/nonexistent.js")).status_code == 404
        assert handle.task.done() is False   # 未命中不报错退出
    finally:
        await stop_http(handle, EXIT_OK)


async def test_t50_web_serve_contract_parity(project_ok, persist_dir, monkeypatch):
    """web 下 POST /agents/<id>/message 与 serve 端点契约完全一致
    （同一骨架）；web 无第三条消息通道。"""
    handle = await start_http(monkeypatch, web_mod, cmd_web, project_ok, persist_dir)
    try:
        r = await handle.client.post("/agents/root/message", json={"text": "你好"})
        assert r.status_code == 200
        body = r.json()
        assert body["message_id"] and body["final_text"] == "alpha-reply"
        assert (await handle.client.get("/healthz")).json() == {"status": "ok"}
        assert (await handle.client.post(
            "/agents/ghost/message", json={"text": "x"})).status_code == 404
        # web 增量端点集断言（封闭集防漂移）
        assert WEB_EXTRA_ENDPOINTS == ("GET /", "GET /assets/*")
    finally:
        await stop_http(handle, EXIT_OK)

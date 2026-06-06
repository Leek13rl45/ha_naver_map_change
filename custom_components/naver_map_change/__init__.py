"""
Naver Map Change Integration for Home Assistant
네이버 지도로 기본 지도 교체 + 버전코드 자동 갱신
"""

import logging
import os
import re
import shutil
import sys
import urllib.request
import json

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.components.persistent_notification import async_create

_LOGGER = logging.getLogger(__name__)

DOMAIN = "naver_map_change"

NAVER_MAP_STYLE_URL = "https://map.pstatic.net/nrb/styles/basic.json"
CARTO_TILE_PATTERN = "basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}"


def get_naver_version() -> str:
    try:
        req = urllib.request.Request(NAVER_MAP_STYLE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            version = data.get("version", "")
            if version:
                _LOGGER.info("네이버 지도 버전코드 획득 성공: %s", version)
                return version
    except Exception as err:
        _LOGGER.warning("네이버 버전코드 획득 실패: %s", err)

    try:
        fallback_url = "https://map.pstatic.net/nrb/styles/basic.json?fmt=jpg&mt=bg.ol.ts.ar.lko"
        req = urllib.request.Request(fallback_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8")
            match = re.search(r'"version"\s*:\s*"([^"]+)"', raw)
            if match:
                return match.group(1)
    except Exception as err2:
        _LOGGER.warning("fallback 버전코드 획득 실패: %s", err2)

    return ""


def build_naver_tile_url(version: str) -> str:
    if version:
        return f"https://map.pstatic.net/nrb/styles/basic/{version}/{{z}}/{{x}}/{{y}}@2x.png?mt=bg.ol.ts.ar.lko"
    return "https://map.pstatic.net/nrb/styles/basic/latest/{z}/{x}/{y}@2x.png?mt=bg.ol.ts.ar.lko"


def find_hass_frontend_path() -> str | None:
    try:
        import hass_frontend
        path = os.path.join(os.path.dirname(hass_frontend.__file__), "frontend_latest")
        if os.path.isdir(path):
            _LOGGER.info("hass_frontend 경로 발견: %s", path)
            return path
    except ImportError:
        pass

    for base in sys.path:
        path = os.path.join(base, "hass_frontend", "frontend_latest")
        if os.path.isdir(path):
            return path

    for pyver in ["3.14", "3.13", "3.12", "3.11", "3.10"]:
        path = f"/usr/local/lib/python{pyver}/site-packages/hass_frontend/frontend_latest"
        if os.path.isdir(path):
            return path

    return None


def find_map_js_file(frontend_path: str) -> str | None:
    try:
        for fname in os.listdir(frontend_path):
            # 순수 .js 파일만 (*.js.map, *.js.br, *.js.gz, *.js.bak 제외)
            if not fname.endswith(".js") or "." in fname[:-3]:
                continue
            full_path = os.path.join(frontend_path, fname)
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                if CARTO_TILE_PATTERN in content:
                    _LOGGER.info("지도 JS 파일 발견: %s", fname)
                    return full_path
            except Exception:
                continue
    except Exception as err:
        _LOGGER.error("JS 파일 탐색 오류: %s", err)
    return None


def patch_js_file(js_path: str, naver_url: str) -> bool:
    backup_path = js_path + ".bak"
    try:
        with open(js_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        if "map.pstatic.net" in content:
            _LOGGER.info("이미 네이버 지도 적용됨 — 버전코드 업데이트")
            new_content = re.sub(
                r"https://map\.pstatic\.net/nrb/styles/basic/[^/\"'`]*/\{z\}/\{x\}/\{y\}[^\"'`\s]*",
                naver_url,
                content
            )
        else:
            new_content = re.sub(
                r"https://\{s\}\.basemaps\.cartocdn\.com/rastertiles/voyager/\{z\}/\{x\}/\{y\}[^\s\"'`]*",
                naver_url,
                content
            )
            new_content = re.sub(
                r"https://basemaps\.cartocdn\.com/rastertiles/voyager/\{z\}/\{x\}/\{y\}[^\s\"'`]*",
                naver_url,
                new_content
            )
            new_content = re.sub(
                r"https://\{s\}\.basemaps\.cartocdn\.com/(?:dark_all|light_all)/\{z\}/\{x\}/\{y\}[^\s\"'`]*",
                naver_url,
                new_content
            )

        if new_content == content:
            _LOGGER.warning("교체할 CARTO URL을 찾지 못했습니다: %s", js_path)
            return False

        if not os.path.exists(backup_path):
            shutil.copy2(js_path, backup_path)
            _LOGGER.info("원본 백업 완료: %s", backup_path)

        with open(js_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        _LOGGER.info("JS 파일 교체 완료: %s", os.path.basename(js_path))
        return True

    except PermissionError:
        _LOGGER.error("파일 쓰기 권한 없음: %s", js_path)
        return False
    except Exception as err:
        _LOGGER.error("JS 파일 교체 중 오류: %s", err)
        return False


def restore_js_file(js_path: str) -> bool:
    backup_path = js_path + ".bak"
    if not os.path.exists(backup_path):
        _LOGGER.warning("백업 파일 없음: %s", backup_path)
        return False
    try:
        shutil.copy2(backup_path, js_path)
        _LOGGER.info("원본 복원 완료: %s", js_path)
        return True
    except Exception as err:
        _LOGGER.error("복원 중 오류: %s", err)
        return False


async def async_setup(hass: HomeAssistant, config: dict) -> bool:

    async def handle_apply_naver_map(call: ServiceCall) -> None:
        _LOGGER.info("=== 네이버 지도 교체 서비스 시작 ===")

        version = await hass.async_add_executor_job(get_naver_version)
        naver_url = build_naver_tile_url(version)
        _LOGGER.info("적용할 네이버 타일 URL: %s", naver_url)

        frontend_path = await hass.async_add_executor_job(find_hass_frontend_path)
        if not frontend_path:
            _LOGGER.error("hass_frontend 경로를 찾을 수 없습니다.")
            async_create(hass,
                "❌ 네이버 지도 적용 실패: hass_frontend 경로를 찾을 수 없습니다.",
                title="Naver Map Change",
                notification_id="naver_map_change_error"
            )
            return

        js_path = await hass.async_add_executor_job(find_map_js_file, frontend_path)
        if not js_path:
            _LOGGER.error("지도 JS 파일을 찾을 수 없습니다: %s", frontend_path)
            async_create(hass,
                f"❌ 네이버 지도 적용 실패: 지도 JS 파일을 찾을 수 없습니다.\n경로: {frontend_path}",
                title="Naver Map Change",
                notification_id="naver_map_change_error"
            )
            return

        success = await hass.async_add_executor_job(patch_js_file, js_path, naver_url)

        if success:
            msg = (
                f"✅ 네이버 지도 적용 완료!\n"
                f"버전코드: {version or '자동'}\n"
                f"변경 파일: {os.path.basename(js_path)}\n\n"
                f"브라우저 캐시를 초기화(Ctrl+Shift+R)하면 지도가 바뀝니다."
            )
            _LOGGER.info("네이버 지도 적용 성공!")
        else:
            msg = "❌ 네이버 지도 적용 실패\n로그를 확인하세요."

        async_create(hass, msg, title="Naver Map Change", notification_id="naver_map_change_result")

    async def handle_restore_map(call: ServiceCall) -> None:
        _LOGGER.info("=== 원본 지도 복원 서비스 시작 ===")

        frontend_path = await hass.async_add_executor_job(find_hass_frontend_path)
        if not frontend_path:
            return

        js_path = await hass.async_add_executor_job(find_map_js_file, frontend_path)

        if js_path:
            backup_path = js_path + ".bak"
        else:
            backup_path = None
            for fname in os.listdir(frontend_path):
                if fname.endswith(".js.bak"):
                    backup_path = os.path.join(frontend_path, fname)
                    js_path = backup_path.replace(".bak", "")
                    break

        if not backup_path or not os.path.exists(backup_path):
            async_create(hass,
                "⚠️ 백업 파일이 없습니다. 이미 원본 상태입니다.",
                title="Naver Map Change",
                notification_id="naver_map_change_restore"
            )
            return

        success = await hass.async_add_executor_job(restore_js_file, js_path)
        msg = (
            "✅ 원본 지도(CARTO) 복원 완료!\n브라우저 캐시를 초기화(Ctrl+Shift+R)하세요."
            if success else "❌ 복원 실패. 로그를 확인하세요."
        )
        async_create(hass, msg, title="Naver Map Change", notification_id="naver_map_change_restore")

    hass.services.async_register(DOMAIN, "apply", handle_apply_naver_map)
    hass.services.async_register(DOMAIN, "restore", handle_restore_map)

    _LOGGER.info("Naver Map Change 통합구성요소 로드 완료")
    return True

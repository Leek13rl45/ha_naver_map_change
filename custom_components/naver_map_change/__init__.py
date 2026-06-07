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
RETINA_PATTERN = '+(t.Browser.retina?"@2x.png":".png")'


def get_naver_version() -> str:
    """네이버 지도 API에서 최신 버전코드를 가져옵니다."""
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


def find_hass_frontend_dirs() -> list[str]:
    """hass_frontend의 frontend_latest와 frontend_es5 경로를 모두 반환합니다."""
    base = None

    try:
        import hass_frontend
        base = os.path.dirname(hass_frontend.__file__)
    except ImportError:
        pass

    if not base:
        for pyver in ["3.14", "3.13", "3.12", "3.11", "3.10"]:
            path = f"/usr/local/lib/python{pyver}/site-packages/hass_frontend"
            if os.path.isdir(path):
                base = path
                break

    if not base:
        for b in sys.path:
            path = os.path.join(b, "hass_frontend")
            if os.path.isdir(path):
                base = path
                break

    if not base:
        return []

    dirs = []
    for sub in ["frontend_latest", "frontend_es5"]:
        full = os.path.join(base, sub)
        if os.path.isdir(full):
            dirs.append(full)
    return dirs


def find_map_js_file(frontend_path: str, pattern: str) -> str | None:
    """패턴이 포함된 JS 파일을 찾습니다."""
    try:
        for fname in os.listdir(frontend_path):
            if not fname.endswith(".js"):
                continue
            # .js.br .js.gz .js.map .js.bak 등 이중확장자 제외
            without_js = fname[:-3]
            if without_js.endswith((".br", ".gz", ".map", ".bak", ".LICENSE")):
                continue
            full_path = os.path.join(frontend_path, fname)
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                if pattern in content:
                    _LOGGER.info("지도 JS 파일 발견: %s", fname)
                    return full_path
            except Exception:
                continue
    except Exception as err:
        _LOGGER.error("JS 파일 탐색 오류: %s", err)
    return None


def patch_js_file(js_path: str, naver_url: str) -> bool:
    """JS 파일에서 CARTO URL을 네이버 URL로 교체하고 brotli 압축합니다."""
    try:
        import brotli
    except ImportError:
        _LOGGER.error("brotli 패키지가 없습니다. pip install brotli 실행 후 재시도하세요.")
        return False

    backup_path = js_path + ".bak"

    try:
        with open(js_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # 이미 네이버로 교체된 경우 버전코드만 업데이트
        if "map.pstatic.net" in content:
            _LOGGER.info("이미 네이버 지도 적용됨 — 버전코드 업데이트")
            new_content = re.sub(
                r"https://map\.pstatic\.net/nrb/styles/basic/[^/\"'`]*/\{z\}/\{x\}/\{y\}@2x\.png\?mt=bg\.ol\.ts\.ar\.lko",
                naver_url,
                content
            )
        else:
            # 1단계: CARTO URL → 네이버 URL 교체
            new_content = content.replace(
                "basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}",
                f"map.pstatic.net/nrb/styles/basic/{naver_url.split('/basic/')[1].split('/')[0]}/{{z}}/{{x}}/{{y}}@2x.png?mt=bg.ol.ts.ar.lko"
            )
            # 2단계: retina 분기 코드 제거 (URL 뒤에 붙는 @2x.png 중복 방지)
            new_content = new_content.replace(
                '+(t.Browser.retina?"@2x.png":".png")', ""
            )

        if new_content == content:
            _LOGGER.warning("교체할 패턴을 찾지 못했습니다: %s", js_path)
            return False

        # 백업 생성 (최초 1회)
        if not os.path.exists(backup_path):
            shutil.copy2(js_path, backup_path)
            _LOGGER.info("원본 백업 완료: %s", backup_path)

        # JS 파일 저장
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        # brotli 압축 파일 재생성
        compressed = brotli.compress(new_content.encode("utf-8"))
        with open(js_path + ".br", "wb") as f:
            f.write(compressed)

        _LOGGER.info("JS + br 파일 교체 완료: %s", os.path.basename(js_path))
        return True

    except PermissionError:
        _LOGGER.error("파일 쓰기 권한 없음: %s", js_path)
        return False
    except Exception as err:
        _LOGGER.error("JS 파일 교체 중 오류: %s", err)
        return False


def restore_js_file(js_path: str) -> bool:
    """백업에서 원본 JS 파일을 복원하고 brotli 재압축합니다."""
    try:
        import brotli
    except ImportError:
        _LOGGER.error("brotli 패키지가 없습니다.")
        return False

    backup_path = js_path + ".bak"
    if not os.path.exists(backup_path):
        return False

    try:
        shutil.copy2(backup_path, js_path)
        with open(js_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        compressed = brotli.compress(content.encode("utf-8"))
        with open(js_path + ".br", "wb") as f:
            f.write(compressed)
        _LOGGER.info("원본 복원 완료: %s", js_path)
        return True
    except Exception as err:
        _LOGGER.error("복원 중 오류: %s", err)
        return False


async def async_setup(hass: HomeAssistant, config: dict) -> bool:

    async def handle_apply_naver_map(call: ServiceCall) -> None:
        _LOGGER.info("=== 네이버 지도 교체 서비스 시작 ===")

        # 1. 최신 버전코드 가져오기
        version = await hass.async_add_executor_job(get_naver_version)
        naver_url = build_naver_tile_url(version)
        _LOGGER.info("적용할 네이버 타일 URL: %s", naver_url)

        # 2. frontend_latest + frontend_es5 경로 탐색
        frontend_dirs = await hass.async_add_executor_job(find_hass_frontend_dirs)
        if not frontend_dirs:
            _LOGGER.error("hass_frontend 경로를 찾을 수 없습니다.")
            async_create(hass,
                "❌ 네이버 지도 적용 실패: hass_frontend 경로를 찾을 수 없습니다.",
                title="Naver Map Change",
                notification_id="naver_map_change_error"
            )
            return

        success_count = 0
        for frontend_path in frontend_dirs:
            # 이미 네이버 적용된 경우 pstatic으로 탐색, 아니면 cartocdn으로 탐색
            pattern = "map.pstatic.net" if True else CARTO_TILE_PATTERN
            js_path = await hass.async_add_executor_job(
                find_map_js_file, frontend_path, CARTO_TILE_PATTERN
            )
            # CARTO 못 찾으면 이미 네이버로 교체된 파일 탐색
            if not js_path:
                js_path = await hass.async_add_executor_job(
                    find_map_js_file, frontend_path, "map.pstatic.net"
                )

            if not js_path:
                _LOGGER.warning("지도 JS 파일을 찾을 수 없습니다: %s", frontend_path)
                continue

            success = await hass.async_add_executor_job(patch_js_file, js_path, naver_url)
            if success:
                success_count += 1

        if success_count > 0:
            msg = (
                f"✅ 네이버 지도 적용 완료! ({success_count}개 파일)\n"
                f"버전코드: {version or '자동'}\n\n"
                f"브라우저 캐시를 초기화(Ctrl+Shift+R)하면 지도가 바뀝니다."
            )
            _LOGGER.info("네이버 지도 적용 성공!")
        else:
            msg = "❌ 네이버 지도 적용 실패\n로그를 확인하세요."

        async_create(hass, msg, title="Naver Map Change", notification_id="naver_map_change_result")

    async def handle_restore_map(call: ServiceCall) -> None:
        _LOGGER.info("=== 원본 지도 복원 서비스 시작 ===")

        frontend_dirs = await hass.async_add_executor_job(find_hass_frontend_dirs)
        if not frontend_dirs:
            return

        success_count = 0
        for frontend_path in frontend_dirs:
            for fname in os.listdir(frontend_path):
                if fname.endswith(".js.bak"):
                    js_path = os.path.join(frontend_path, fname.replace(".bak", ""))
                    success = await hass.async_add_executor_job(restore_js_file, js_path)
                    if success:
                        success_count += 1

        msg = (
            f"✅ 원본 지도 복원 완료! ({success_count}개 파일)\n브라우저 캐시를 초기화(Ctrl+Shift+R)하세요."
            if success_count > 0 else "⚠️ 백업 파일이 없습니다. 이미 원본 상태입니다."
        )
        async_create(hass, msg, title="Naver Map Change", notification_id="naver_map_change_restore")

    hass.services.async_register(DOMAIN, "apply", handle_apply_naver_map)
    hass.services.async_register(DOMAIN, "restore", handle_restore_map)

    _LOGGER.info("Naver Map Change 통합구성요소 로드 완료")
    return True

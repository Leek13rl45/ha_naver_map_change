"""
Naver Map Change Integration for Home Assistant
네이버 지도로 기본 지도 교체 + 버전코드 자동 갱신
"""

import logging
import os
import re
import shutil
import urllib.request
import json

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)

DOMAIN = "naver_map_change"

# 네이버 지도 버전코드를 얻어오는 URL
NAVER_MAP_STYLE_URL = "https://map.pstatic.net/nrb/styles/basic.json"

# HA 프론트엔드 JS 경로 (HA 버전에 따라 다를 수 있음)
HA_FRONTEND_JS_PATHS = [
    "/usr/src/homeassistant/homeassistant/components/frontend/",
    "/home/homeassistant/.local/lib/python3.12/site-packages/homeassistant/components/frontend/",
    "/home/homeassistant/.local/lib/python3.11/site-packages/homeassistant/components/frontend/",
    "/home/homeassistant/.local/lib/python3.10/site-packages/homeassistant/components/frontend/",
]

# 교체 대상 OSM 타일 URL 패턴
OSM_TILE_PATTERN = r"https://\{s\}\.tile\.openstreetmap\.org/\{z\}/\{x\}/\{y\}\.png"
OSM_TILE_FALLBACK = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def get_naver_version() -> str:
    """네이버 지도 API에서 최신 버전코드를 가져옵니다."""
    try:
        req = urllib.request.Request(
            NAVER_MAP_STYLE_URL,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            version = data.get("version", "")
            if version:
                _LOGGER.info("네이버 지도 버전코드 획득 성공: %s", version)
                return version
    except Exception as err:
        _LOGGER.warning("네이버 버전코드 획득 실패, 기본값 사용: %s", err)

    # 실패 시 fallback: 날짜 기반으로 pstatic URL 직접 조회
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
    """버전코드로 네이버 타일 URL을 생성합니다."""
    if version:
        return f"https://map.pstatic.net/nrb/styles/basic/{version}/{{z}}/{{x}}/{{y}}@2x.png?mt=bg.ol.ts.ar.lko"
    # 버전코드 없이도 동작하는 대체 URL
    return "https://map.pstatic.net/nrb/styles/basic/latest/{z}/{x}/{y}@2x.png?mt=bg.ol.ts.ar.lko"


def find_ha_frontend_path() -> str | None:
    """HA 프론트엔드 JS 디렉토리를 찾습니다."""
    # homeassistant 패키지 경로 동적 탐색
    try:
        import homeassistant
        ha_path = os.path.dirname(homeassistant.__file__)
        frontend_path = os.path.join(ha_path, "components", "frontend")
        if os.path.isdir(frontend_path):
            _LOGGER.debug("HA 프론트엔드 경로 발견: %s", frontend_path)
            return frontend_path
    except Exception as err:
        _LOGGER.debug("동적 경로 탐색 실패: %s", err)

    # 하드코딩 경로 fallback
    for path in HA_FRONTEND_JS_PATHS:
        if os.path.isdir(path):
            return path

    return None


def find_map_js_file(frontend_path: str) -> str | None:
    """지도 관련 JS 파일을 찾습니다."""
    for fname in os.listdir(frontend_path):
        if fname.endswith(".js") and ("map" in fname.lower() or "chunk" in fname.lower()):
            full_path = os.path.join(frontend_path, fname)
            # 파일 내용에 OSM URL이 있는지 확인
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    if "tile.openstreetmap.org" in content or "openstreetmap" in content.lower():
                        return full_path
            except Exception:
                continue
    return None


def patch_js_file(js_path: str, naver_url: str) -> bool:
    """JS 파일에서 OSM URL을 네이버 URL로 교체합니다."""
    backup_path = js_path + ".bak"

    try:
        with open(js_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # 이미 네이버 URL로 교체된 경우
        if "map.pstatic.net" in content:
            _LOGGER.info("이미 네이버 지도가 적용되어 있습니다.")
            # 버전코드만 업데이트
            new_content = re.sub(
                r"https://map\.pstatic\.net/nrb/styles/basic/[^/\"']*/\{z\}/\{x\}/\{y\}[^\"']*",
                naver_url,
                content
            )
        else:
            # OSM URL을 네이버 URL로 교체
            new_content = content.replace(
                "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                naver_url
            )
            new_content = new_content.replace(
                "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                naver_url
            )
            # 추가 패턴 처리
            new_content = re.sub(
                r"https?://\{?s\}?\.?tile\.openstreetmap\.org/\{z\}/\{x\}/\{y\}\.png",
                naver_url,
                new_content
            )

        if new_content == content:
            _LOGGER.warning("교체할 OSM URL을 찾지 못했습니다: %s", js_path)
            return False

        # 백업 생성
        if not os.path.exists(backup_path):
            shutil.copy2(js_path, backup_path)
            _LOGGER.info("원본 파일 백업: %s", backup_path)

        with open(js_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        _LOGGER.info("JS 파일 교체 완료: %s", js_path)
        return True

    except PermissionError:
        _LOGGER.error(
            "파일 쓰기 권한 없음: %s\n"
            "HA를 root 또는 적절한 권한으로 실행 중인지 확인하세요.", js_path
        )
        return False
    except Exception as err:
        _LOGGER.error("JS 파일 교체 중 오류: %s", err)
        return False


def restore_js_file(js_path: str) -> bool:
    """백업에서 원본 JS 파일을 복원합니다."""
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
    """Integration 설정."""

    async def handle_apply_naver_map(call: ServiceCall) -> None:
        """네이버 지도 적용 서비스 핸들러."""
        _LOGGER.info("=== 네이버 지도 교체 서비스 시작 ===")

        # 1. 최신 버전코드 가져오기
        version = await hass.async_add_executor_job(get_naver_version)
        naver_url = build_naver_tile_url(version)
        _LOGGER.info("적용할 네이버 타일 URL: %s", naver_url)

        # 2. HA 프론트엔드 경로 탐색
        frontend_path = await hass.async_add_executor_job(find_ha_frontend_path)
        if not frontend_path:
            _LOGGER.error(
                "HA 프론트엔드 경로를 찾을 수 없습니다. "
                "지원되는 설치 방법: HAOS, venv, Docker"
            )
            hass.components.persistent_notification.create(
                "❌ 네이버 지도 적용 실패: 프론트엔드 경로를 찾을 수 없습니다.",
                title="Naver Map Change",
                notification_id="naver_map_change_error"
            )
            return

        # 3. 지도 JS 파일 탐색
        js_path = await hass.async_add_executor_job(find_map_js_file, frontend_path)
        if not js_path:
            _LOGGER.error("지도 JS 파일을 찾을 수 없습니다: %s", frontend_path)
            hass.components.persistent_notification.create(
                "❌ 네이버 지도 적용 실패: 지도 JS 파일을 찾을 수 없습니다.\n"
                f"경로: {frontend_path}",
                title="Naver Map Change",
                notification_id="naver_map_change_error"
            )
            return

        # 4. JS 파일 패치
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
            msg = (
                "❌ 네이버 지도 적용 실패\n"
                "로그를 확인하세요."
            )

        hass.components.persistent_notification.create(
            msg,
            title="Naver Map Change",
            notification_id="naver_map_change_result"
        )

    async def handle_restore_map(call: ServiceCall) -> None:
        """원본(OSM) 지도 복원 서비스 핸들러."""
        _LOGGER.info("=== 원본 지도 복원 서비스 시작 ===")

        frontend_path = await hass.async_add_executor_job(find_ha_frontend_path)
        if not frontend_path:
            _LOGGER.error("프론트엔드 경로를 찾을 수 없습니다.")
            return

        js_path = await hass.async_add_executor_job(find_map_js_file, frontend_path)

        # 백업 파일로 복원
        if js_path:
            backup_path = js_path + ".bak"
        else:
            # 이미 OSM으로 돌아간 경우 .bak 파일 탐색
            backup_path = None
            for fname in os.listdir(frontend_path):
                if fname.endswith(".js.bak"):
                    backup_path = os.path.join(frontend_path, fname)
                    js_path = backup_path.replace(".bak", "")
                    break

        if not backup_path or not os.path.exists(backup_path):
            hass.components.persistent_notification.create(
                "⚠️ 백업 파일이 없습니다. 이미 원본 상태이거나 백업이 존재하지 않습니다.",
                title="Naver Map Change",
                notification_id="naver_map_change_restore"
            )
            return

        success = await hass.async_add_executor_job(restore_js_file, js_path)
        msg = "✅ 원본 지도(OSM) 복원 완료!\n브라우저 캐시를 초기화(Ctrl+Shift+R)하세요." if success else "❌ 복원 실패. 로그를 확인하세요."
        hass.components.persistent_notification.create(
            msg,
            title="Naver Map Change",
            notification_id="naver_map_change_restore"
        )

    # 서비스 등록
    hass.services.async_register(DOMAIN, "apply", handle_apply_naver_map)
    hass.services.async_register(DOMAIN, "restore", handle_restore_map)

    _LOGGER.info("Naver Map Change 통합구성요소 로드 완료")
    return True

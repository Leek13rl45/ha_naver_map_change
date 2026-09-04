# 02. HA 플랫폼 사실 정리 (2026.9 기준)

> 조사 시점: 2026-09-04. 각 항목에 **[직접검증]** / **[조사]** 표시로 신뢰도를 구분했다.
> **[직접검증]** = 이 문서 작성 중 원본 소스/문서를 fetch 해 확인. **[조사]** = 하청 에이전트가
> 웹 근거와 함께 보고했으나 본 문서 작성자가 원본을 재확인하지 않음. **[미확인]** = 근거 부족.

---

## 1. 버전·런타임

| 항목 | 값 | 신뢰도 |
|---|---|---|
| 최신 Core | 2026.9.0 (2026-09-02 릴리스) | [조사] |
| 최신 Supervisor | 2026.09.0 | [조사] |
| Core 요구 Python | `>= 3.14.2` (2026.3에서 3.13 → 3.14 상향) | [조사] `pyproject.toml` 근거 |
| 공식 지원 설치 방식 | **HA OS, HA Container 두 가지뿐** | [조사] |
| 지원 종료 | **HA Core(venv), HA Supervised 는 2025.12 지원 종료** | [조사] |

근거: https://github.com/home-assistant/core/releases/tag/2026.9.0 ,
https://www.home-assistant.io/blog/2025/05/22/deprecating-core-and-supervised-installation-methods-and-32-bit-systems/

> **설계 함의**: 재설계는 HA OS / HA Container 두 방식만 지원 대상으로 삼는다. 구 코드의
> `python3.10~3.14 site-packages 경로 순차 탐색` 같은 venv 대응 로직은 전부 폐기 대상이다.

## 2. 파일시스템 영속성 — 재설계의 제1 제약

**[조사]** HAOS/Container 에서 영속되는 경로는 **바인드 마운트된 볼륨뿐**이다:
`/config`, `/share`, `/media`, `/backup`, `/ssl`, `/addons`.
호스트 실제 위치는 데이터 파티션 `/mnt/data/supervisor/` 하위.

`site-packages/hass_frontend` 는 **컨테이너 이미지 레이어**에 속한다.

| 이벤트 | site-packages 수정 유지? | 이유 |
|---|---|---|
| 컨테이너 재시작 | **유지** | 같은 컨테이너의 overlay 쓰기 레이어가 보존됨 |
| 호스트 재부팅 | **유지** | 컨테이너 재시작과 동일 |
| **HA Core 업데이트** | **소멸** | 새 이미지로 컨테이너를 교체 |
| 컨테이너 재생성(재설치/복구) | **소멸** | 동일 |

컨테이너 파일시스템이 read-only 로 강제되지는 않으므로 **쓰기는 가능하다**. read-only 배포는
제안(architecture#490)에 머물고 채택되지 않았다. 즉 "쓸 수는 있으나 업데이트하면 사라진다"가 정확한 서술이다.

기타:
- `/config/www/` → `http://<host>:8123/local/` 로 매핑. **[조사]** (공식 http 통합 문서)
- 커스텀 통합의 `manifest.json` `requirements` 패키지는 venv 미사용 시 **`/config/deps`** 에 설치. **[조사]**
- 컨테이너 내부 site-packages 를 사용자가 임의로 수정하는 것은 **공식 지원 경로가 아니다.** **[조사]**

> **설계 함의 (결정적)**: 영구적으로 적용되는 방식은 **`/config` 안에만 쓰는 것**이다.
> `/config/custom_components/<domain>/` 에 상주하는 커스텀 통합은 HA 업데이트와 무관하게 살아남는다.
> 따라서 재설계의 모든 산출물(파이썬 코드, 프론트엔드 JS, 설정)은 통합 폴더 안에 있어야 하며,
> **site-packages 를 절대 건드리지 않는다.**

## 3. 2026.9 지도 아키텍처 (전면 교체됨)

**[직접검증]** — `home-assistant/frontend` dev 브랜치와 `home-assistant/core` dev 브랜치 원본 fetch.

### 3.1 무엇이 바뀌었나

- CARTO voyager 래스터 타일이 **제거**되고 **MapLibre GL + OSM Shortbread 벡터 타일**로 교체.
  (배경: CARTO 가 API 키를 요구하며 워터마크를 표시하기 시작한 사태)
- 브라우저가 upstream 타일 서버를 직접 호출하지 않는다. **HA Core 가 서버측 프록시**로 중개한다.
- 관련 PR: frontend#53816, core#180441 / 릴리스: 2026.9

### 3.2 프론트엔드: `src/common/map/base-layer.ts`

```ts
const VECTOR_STYLES = { light: "/static/map/light.json", dark: "/static/map/dark.json" } as const;
const RTL_TEXT_PLUGIN_URL = "/static/map/mapbox-gl-rtl-text.js";
const RASTER_TILE_URL = `${MAP_TILES_PATH}/raster/{z}/{x}/{y}.png?token={token}`;
const DEMO_RASTER_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const MAP_MIN_ZOOM = 1;
export const MAP_MAX_ZOOM = 20;
const RASTER_MAX_NATIVE_ZOOM = 19;
```

동작 흐름 — `createBaseLayer(leaflet, map, darkMode, token)`:

```
supportsWebGL2() ?
  ├─ yes → import("@maplibre/maplibre-gl-leaflet"), import("maplibre-gl")
  │        → createVectorLayer(): loadStyle(VECTOR_STYLES[light|dark]) 로 스타일 JSON을 fetch,
  │          maplibreGL({ style, transformRequest: url => ({ url: withMapTilesToken(url), ... }) })
  │        → 실패(throw)하면 undefined 반환
  └─ no / 벡터 실패 → createRasterLayer(): leaflet.tileLayer(mapTilesUrl(RASTER_TILE_URL), {
                        attribution: OSM_ATTRIBUTION, maxZoom: 20, maxNativeZoom: 19, token })
```

핵심 사실:
- **Leaflet 은 여전히 쓰인다.** 마커·클러스터링·존 편집·경로·스케일 컨트롤은 Leaflet 이고,
  **베이스맵 타일 렌더링만** MapLibre 로 전환됐다. `maplibre-gl-leaflet` 어댑터로 붙는다.
- 벡터 경로가 기본, 래스터는 **WebGL2 미지원 브라우저용 폴백**.
- `@2x` 레티나 분기는 **삭제됐다** (소스 주석: "OSM serves no @2x variant").
- `loadStyle()` 은 스타일 JSON 을 fetch 한 뒤 `style.sprite` 를 `mapTilesUrl()` 로 절대화한다.
  `sprite` 가 없으면(undefined) 그냥 통과한다 — **래스터 전용 스타일을 주입할 때 sprite/glyphs 는 불필요.**
- 벡터 레이어가 403/404/네트워크 실패를 받으면 `refreshMapTilesToken()` 후 스타일 재적용
  (30초 스로틀). 토큰 만료 자동 복구 로직이 이미 있다.

### 3.3 프론트엔드: `src/data/map_tiles.ts` — 토큰 체계

```ts
export const MAP_TILES_PATH = "/api/map_tiles";
const TOKEN_REFRESH_MS = 20 * 60 * 1000;
// WS 명령 { type: "map_tiles/access_token" } → { token }
export const mapTilesUrl = (path) => path.startsWith("/") ? `${instanceOrigin()}${path}` : path;
export const withMapTilesToken = (url) => {
  ...
  if (!parsed.pathname.startsWith(`${MAP_TILES_PATH}/`)) return parsed.href;   // ← 접두사 검사
  const onInstance = new URL(`${instanceOrigin()}${parsed.pathname}${parsed.search}`);
  if (token) onInstance.searchParams.set("token", token);
  return onInstance.href;
};
```

> **★ 재설계의 열쇠 [직접검증]**: `withMapTilesToken()` 은 pathname 이 `/api/map_tiles/` 로
> 시작하는 **모든** URL에 유효 토큰을 붙인다. 특정 하위 경로 화이트리스트가 없다.
> 따라서 **커스텀 통합이 `/api/map_tiles/<고유이름>/{z}/{x}/{y}.png` 에 자체 뷰를 등록하면,
> 코어의 `transformRequest` 가 우리 타일 요청에도 유효한 회전 토큰을 자동으로 붙여준다.**
> 별도 토큰 발급·WS 명령·JS 내 비밀값 埋め込み이 전부 불필요해진다.

### 3.4 코어: `homeassistant/components/map_tiles/`

파일: `__init__.py`(2585B), `cache.py`(4324B), `const.py`(3550B), `views.py`(12291B), `manifest.json`

`__init__.py` 의 docstring 이 **프록시가 왜 필요한지**를 명시한다 — 우리 설계의 근거와 동일하다:

> "A proxy is needed because the OSMF tile policy wants requests identified via `User-Agent` or
> `Referer`, and a browser can send neither: both are forbidden header names, and the default
> referrer (the page origin) would expose the user's Nabu Casa installation URL."

`async_setup()` 이 하는 일:
```python
access_tokens: deque[str] = deque([secrets.token_hex(TOKEN_SIZE)], maxlen=2)   # 토큰 2개 유지
hass.data[DATA_ACCESS_TOKENS] = access_tokens                                   # HassKey("map_tiles")
async_track_time_interval(hass, _rotate_token, TOKEN_CHANGE_INTERVAL, cancel_on_shutdown=True)  # 30분
cache = MapTilesCache(hass)
for view in (MapTilesTileJsonView, MapTilesVectorView, MapTilesRasterView,
             MapTilesGlyphsView, MapTilesSpriteIndexView, MapTilesSpriteSheetView):
    hass.http.register_view(view(hass, cache))
websocket_api.async_register_command(hass, ws_access_token)
```
- 토큰은 30분마다 회전하되 **2개가 동시 유효** → 실질 유효기간 30~60분.
- `CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)` — YAML 설정 항목 없음.

`const.py` 주요 상수:
```python
VECTOR_URL   = "https://vector.openstreetmap.org"
RASTER_URL   = "https://tile.openstreetmap.org"
TILEJSON_URL = f"{VECTOR_URL}/shortbread_v1/tilejson.json"
UPSTREAM_TIMEOUT = ClientTimeout(total=10)
UPSTREAM_HEADERS = {"User-Agent": f"HomeAssistant/{__version__} (+...; abuse@home-assistant.io)",
                    "Accept-Encoding": "gzip"}
TILE_TTL = 7*24*3600 ; ASSET_TTL = 30*24*3600 ; TILEJSON_TTL = 3600
TILE_MAX_AGE = 7*24*3600 ; ASSET_MAX_AGE = 30*24*3600 ; TILEJSON_MAX_AGE = 300
CACHE_MAX_BYTES = 32*1024*1024          # 인메모리 (SD카드 수명 고려, 디스크 캐시 안 씀)
MAX_FETCH_BYTES = 8*1024*1024 ; MAX_DECOMPRESSED_BYTES = 32*1024*1024
MAX_CONCURRENT_FETCHES = 16
VECTOR_MAX_ZOOM = 14 ; RASTER_MAX_ZOOM = 19
TOKEN_SIZE = 32 ; TOKEN_CHANGE_INTERVAL = timedelta(minutes=30)
```

`views.py` 의 뷰 기반 클래스 `_MapTilesView` — **우리 프록시 뷰의 참조 구현**:
```python
class _MapTilesView(HomeAssistantView):
    requires_auth = False          # <img> 는 Authorization 헤더를 못 보냄

    def _authenticate(self, request):
        access_tokens = self._hass.data[DATA_ACCESS_TOKENS]
        if request[KEY_AUTHENTICATED] or request.query.get("token") in access_tokens:
            return
        if hdrs.AUTHORIZATION in request.headers:
            raise web.HTTPUnauthorized      # 실제 Bearer 시도 → ban 미들웨어가 카운트
        raise web.HTTPForbidden             # 만료 토큰 → 사용자 IP 밴 방지용 403

    async def _async_fetch(self, url):
        session = async_get_clientsession(self._hass)
        async with session.get(url, headers=UPSTREAM_HEADERS, timeout=UPSTREAM_TIMEOUT,
                               auto_decompress=False) as response:
            ...  # 64KB 청크로 읽으며 MAX_FETCH_BYTES 초과 시 거부
```
캐시 응답 헤더: `Cache-Control: private, max-age=<max_age>`, upstream 인코딩 그대로 전달.

### 3.5 로드 보장 관계 [직접검증]

```
frontend/manifest.json  dependencies: [api, auth, config, device_automation, diagnostics,
                                       file_upload, http, lovelace, map_tiles, onboarding,
                                       repairs, search, system_log, websocket_api]
                        requirements:  ["home-assistant-frontend==20260826.4"]
map_tiles/manifest.json dependencies: [http, websocket_api]
                        integration_type: system, config_flow: false, quality_scale: internal
```
`map_tiles` 는 `default_config` 에 직접 나열되지는 않지만 **`frontend` 의 dependency** 이므로
프론트엔드가 있는 모든 설치에서 항상 로드된다. 즉 `hass.data["map_tiles"]` 토큰 저장소와
`/api/map_tiles/*` 라우트의 존재를 실질적으로 가정할 수 있다.

## 4. 커스텀 통합 규격 (2026)

**[조사]** — 근거 URL 은 각 항목에.

### 4.1 `manifest.json`
필수: `domain`, `name`, `codeowners`, `documentation`, `requirements`, `dependencies`,
`integration_type`, `iot_class`. **커스텀 통합 전용 필수**: `version` (AwesomeVersion 호환).
HACS 추가 요구: `issue_tracker`.
`iot_class` 허용값: `assumed_state | cloud_polling | cloud_push | local_polling | local_push | calculated`.
`quality_scale` 는 코어 통합 등급 시스템이라 커스텀에서는 실질 의미 없음.
→ https://developers.home-assistant.io/docs/creating_integration_manifest/

### 4.2 UI 설치 요건
`"config_flow": true` + `config_flow.py`(ConfigFlow 상속, `async_step_user`) +
`async_setup_entry`/`async_unload_entry`. **`async_setup(hass, config)` 만 있는 통합은
"통합구성요소 추가" 목록에 나타나지 않는다.**
→ https://developers.home-assistant.io/docs/config_entries_config_flow_handler

### 4.3 ConfigEntry 패턴
`hass.data[DOMAIN]` 대신 **`entry.runtime_data`** 사용이 권장(2024.4.30 도입).
`ConfigEntry` 가 제네릭이므로 `type MyConfigEntry = ConfigEntry[MyData]` 표기 가능. unload 시 자동 정리.
→ https://developers.home-assistant.io/blog/2024/04/30/store-runtime-data-inside-config-entry/

### 4.4 서비스(액션)
- 도메인 레벨 서비스는 `hass.services.async_register()`.
- **등록 위치는 `async_setup()`** — config entry 로드 여부와 무관하게 자동화가 서비스를 참조·검증할
  수 있어야 하므로 공식 권고. `async_setup_entry` 에서 등록하지 않는다.
- `SupportsResponse.OPTIONAL | ONLY` 로 응답 반환 가능(JSON 직렬화 가능 dict).
- `services.yaml` 은 `fields`(selector/required/example/default), `target` 구조.
→ https://developers.home-assistant.io/docs/dev_101_services/

### 4.5 번역
- **`strings.json` 은 코어 전용(Lokalise 빌드타임 기능)이며 커스텀 통합에서 사용 금지.**
- 커스텀 통합은 `translations/en.json`, `translations/ko.json` 에 flat 텍스트를 직접 작성.
  `[%key:...%]` 참조 불가. `config`, `options`, `services`, `exceptions` 키 사용.
→ https://developers.home-assistant.io/docs/internationalization/custom_integration/

### 4.6 deprecation / 제거된 API
| 구 API | 신 API | 비고 |
|---|---|---|
| `hass.http.register_static_path` | `await hass.http.async_register_static_paths([StaticPathConfig(...)])` | 2024.6 도입, 구 API **2025.7 제거** |
| `hass.components.X` | `from homeassistant.components.X import ...` | 2024.3 deprecated, 2024.9 제거 |
| `urllib.request` | `async_get_clientsession()` (aiohttp) / `get_async_client()` (httpx) | 권장 |
→ https://developers.home-assistant.io/blog/2024/06/18/async_register_static_paths/ ,
  https://developers.home-assistant.io/blog/2024/02/27/deprecate-bind-hass-and-hass-components/

### 4.7 이벤트 루프 블로킹 탐지 (2024.7.0+)
탐지 대상: `open()`, `Path.read_text/write_text/read_bytes/write_bytes()`,
`os.listdir/walk/scandir`, `glob.glob/iglob`, `os.stat`, `urllib`(putrequest),
`time.sleep`, `import_module`, SSL 인증서 로드.
탐지 시 `Detected blocking call ... by integration` 로그. 강제 예외화 방향.
→ https://developers.home-assistant.io/docs/asyncio_blocking_operations/

### 4.8 프론트엔드 JS 주입 공식 경로
```python
# homeassistant/components/frontend/__init__.py  [조사]
def add_extra_js_url(hass: HomeAssistant, url: str, es5: bool = False) -> None:
    key = DATA_EXTRA_JS_URL_ES5 if es5 else DATA_EXTRA_MODULE_URL
    hass.data[key].add(url)
```
YAML 대안:
```yaml
frontend:
  extra_module_url: [/local/my_module.js]
  extra_js_url_es5: [/local/my_es5.js]
```
정적 서빙:
```python
from homeassistant.components.http import StaticPathConfig
await hass.http.async_register_static_paths([
    StaticPathConfig("/api/my_integration/static", str(files_path), should_cache),
])
```
`frontend_es5` 빌드 폐지 여부·시점은 **[미확인]** — `extra_js_url_es5` 옵션은 문서상 여전히 유효.

### 4.9 HACS
- 커스텀 통합: `custom_components/<domain>/` 구조, `manifest.json` 에 `domain/documentation/
  issue_tracker/codeowners/name/version` 필수. 릴리스 태그 권장(있으면 최근 5개 선택 UI).
- 플러그인(Lovelace 카드): `.js` 가 저장소 루트 또는 `dist/` 에, 파일명은 저장소명과 일치
  (`lovelace-` 접두사 제거 허용). 검색 순서 `dist/` → 최신 릴리스 → 루트.
- HACS 다운로드 플러그인은 `www/community/` 에 놓이고 `/local/community/...` 와 `/hacsfiles/...`
  양쪽으로 서빙(후자만 HACS 전용 캐시 최적화).
- 검증: `home-assistant/actions` 의 `hassfest` GitHub Action + HACS validation action.
→ https://www.hacs.xyz/docs/publish/integration/

## 5. 이 통합이 참고할 선행 사례

| 프로젝트 | 접근법 | 상태 | 시사점 |
|---|---|---|---|
| [nathan-gs/ha-map-card](https://github.com/nathan-gs/ha-map-card) | 별도 Lovelace 카드. **`tile_layer_url` 옵션으로 임의 타일 URL 지정 지원** | 유지보수 활발 | 카드 교체 방식의 검증된 사례. 단 기본 `map` 카드/패널/more-info 는 못 바꿈. [issue #94](https://github.com/nathan-gs/ha-map-card/issues/94) 가 정확히 "네이버 지도 버전코드 때문에 템플릿 필요" 요청 |
| [miumida/map_change](https://github.com/miumida/map_change) | 프론트엔드 정적 리소스 직접 패치로 추정 | 2024-10 이후 정지 | 현재 저장소와 같은 계열. 같은 이유로 2026.9 에서 무효화 |

**[조사]** `customElements.get("hui-map-card")` 서브클래싱은 알려진 일반 패턴이나, map 카드 전용
공개 사례는 발견되지 않음. Leaflet `L.TileLayer.prototype` 패치 사례도 **[미확인]**.

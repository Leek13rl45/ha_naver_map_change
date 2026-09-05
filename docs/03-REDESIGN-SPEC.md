# 03. 재설계 명세서 (구현 담당 AI용)

> 이 문서는 **구현 지시서**다. `01-AS-IS-ANALYSIS.md`(현황), `02-HA-PLATFORM-2026.md`(플랫폼 사실),
> `04-BASEMAP-PROVIDERS.md`(타일 제공자)를 먼저 읽고 이 문서를 따른다.
> 명세와 사실 문서가 충돌하면 **사실 문서(02)가 우선**이며, 충돌을 발견하면 구현을 멈추고 보고한다.

> ⚠️ **이 문서는 구현 착수 시점(v2.0.0 이전)의 지시서이며, 그대로 갱신되지 않는다.**
> 실측과 실사용으로 뒤집힌 항목이 여럿 있으므로 **현재 코드의 근거는 `05-UPSTREAM-FINDINGS.md`** 다.
> 이미 확인된 이 문서의 오류:
> - §3.1/§3.4 타일 포맷 `.png` 가정 → 네이버는 `.jpg`/`.png` 둘 다 주지만 **`.png` 를 쓴다**(05 §3 정정)
> - §3.2 `@2x` 미사용 결정 → **뒤집혔다.** Retina 화면 화질 문제로 DPR 인식 `@2x` 를 구현했다(05 §3)
> - 레이어 선택자 `mt=bg.ol.ts.ar.lko` 가 이 문서에 없다 → 없으면 버스정류장·지하철출구가 빠진다(05 §3)
> - §1.2 프록시가 필요한 이유 2개(CORS·식별 헤더) → **둘 다 사실이 아니다**(05 §6 에 근거 교체)
> - §3.6 `StaticPathConfig(should_cache=...)` → 실제 인자명은 `cache_headers`
> - §3.6 "`add_extra_js_url` 은 해제 API 가 없다" → `remove_extra_js_url` 이 **존재한다**
> - §3.8 `manifest.json` 예시의 키 순서 → hassfest 가 요구하는 `domain`, `name`, 알파벳순이 아니다
> - §3.2/§3.4 의 dark 관련 서술과 `05` 의 **F8("네이버에 dark 계열 없음")** → 🚨 **오류다.**
>   다크 계열은 존재하며 스타일명 접두사가 `d` 다(`dbasic`). 최초 조사가 `dark`/`night`/`gray` 같은
>   영어 단어 후보만 찍어보고 404 를 "계열 없음" 으로 읽은 것이 원인이다. v2.2.0(D15)에서
>   `url_template_dark` / `url_template_dark_retina` 를 `dbasic` 으로 채웠다.
>   근거와 실측표는 `05` §1 의 "F8 정정" 에 있다.

---

## 0. 절대 제약 (위반 시 구현 반려)

1. **`/config` 밖에 절대 쓰지 않는다.** `site-packages`, `hass_frontend`, 컨테이너 시스템 경로에
   대한 **쓰기·백업·압축·삭제 코드를 일절 작성하지 않는다.** 구 구현의
   `find_hass_frontend_dirs / find_map_js_file / patch_js_file / restore_js_file / recompress_js`
   는 전부 **삭제**한다. 이식하지 않는다.
2. **`brotli` / `gzip` 재압축 코드를 만들지 않는다.** `requirements` 는 빈 배열을 유지한다
   (`aiohttp`는 HA가 이미 제공).
3. **실패는 조용히 기본 지도로 폴백한다.** 프론트엔드 JS의 모든 진입점은 `try/catch` 로 감싸고,
   예외 시 원본 동작(OSM 지도)을 그대로 통과시킨다. **지도가 백지가 되는 실패는 허용하지 않는다.**
4. **이벤트 루프에서 blocking I/O 금지.** 파일 접근·`os.listdir`·`urllib` 사용 금지.
   HTTP는 `async_get_clientsession()`, 파일은 setup 시 1회 `async_add_executor_job`.
5. **최소 지원 버전은 HA 2026.9.0.** 그 이전 버전은 지도 아키텍처가 달라 이 설계가 성립하지 않는다.
   `hacs.json` 의 `homeassistant` 와 config flow 의 버전 가드에 반영한다.
6. `strings.json` 을 만들지 않는다(코어 전용). 번역은 `translations/en.json`, `translations/ko.json`.

## 1. 설계 개요

### 1.1 무엇을 바꾸는가

| | AS-IS | TO-BE |
|---|---|---|
| 개입 지점 | site-packages 의 minify JS 파일을 덮어쓰기 | ① 자체 타일 프록시 뷰 ② 스타일 JSON 엔드포인트 ③ 주입 JS 30줄 |
| 영속성 | HA 업데이트 시 소멸 | `/config` 상주 → **영구** |
| 사용자 조작 | 서비스 호출 + 강력 새로고침 (업데이트마다 반복) | 없음 (설치·재시작 1회) |
| 실패 모드 | 번들 손상 → 지도 백지 | 기본 OSM 지도로 폴백 |
| 브라우저→네이버 직접 호출 | 예 (MapLibre 경로에서 CORS 로 불가) | 아니오 (서버가 프록시) |
| 제공자 | 네이버 고정 | provider 레지스트리로 교체 가능 |

### 1.2 왜 서버측 프록시가 **필수**인가

두 가지 독립적인 이유가 있고, 둘 다 회피 불가다.

1. **CORS** — 2026.9 의 기본 경로는 MapLibre 이고, MapLibre 는 래스터 타일을 워커에서 `fetch` 로
   받는다. `map.pstatic.net` 은 `Access-Control-Allow-Origin` 을 주지 않으므로 브라우저 직접 호출은
   실패한다. (구 Leaflet `<img>` 방식에서는 통하던 것이 통하지 않는다.)
2. **요청 식별 헤더** — 코어 `map_tiles/__init__.py` 의 docstring 이 같은 논리를 명시한다:
   `User-Agent` / `Referer` 는 브라우저가 설정할 수 없는 forbidden header 이고, 기본 referrer 는
   사용자의 Nabu Casa URL 을 노출한다. 서버만이 올바른 헤더를 붙일 수 있다.

부수 이득: 타일 캐시로 upstream 부하 감소, API 키를 브라우저에 노출하지 않음,
네이버 버전코드 갱신이 서버측 관심사로 완전히 이동(파일 재작성이 사라짐).

### 1.3 왜 `/api/map_tiles/` 하위에 등록하는가 — 설계의 핵심

`02-HA-PLATFORM-2026.md` §3.3 에서 직접검증한 사실:

```ts
export const withMapTilesToken = (url: string): string => {
  ...
  if (!parsed.pathname.startsWith(`${MAP_TILES_PATH}/`)) return parsed.href;
  if (token) onInstance.searchParams.set("token", token);
  return onInstance.href;
};
```

MapLibre 의 `transformRequest` 가 모든 타일 요청에 이 함수를 적용한다. pathname 이
`/api/map_tiles/` 로 시작하면 **하위 경로가 무엇이든** 유효한 회전 토큰이 붙는다.

따라서 우리 타일 뷰를 `/api/map_tiles/naver_map_change/...` 에 등록하면:
- 토큰 발급 WS 명령을 직접 만들 필요 없음
- 30분 회전·2개 동시 유효·403 자동 복구를 코어 로직이 그대로 처리
- 정적 JS 에 비밀값을 심을 필요 없음 (심으면 미인증 사용자에게 노출됨)

**대가**: 코어 `map_tiles` 통합의 URL 네임스페이스에 얹히는 결합이 생긴다. 완화책은 §6.3.

### 1.4 데이터 흐름

```
브라우저                                    HA (custom_components/naver_map_change)
────────                                    ────────────────────────────────────────
ha-map 렌더
 └ createBaseLayer()
    └ loadStyle("/static/map/light.json")
        │  ← 주입된 JS가 fetch 를 가로채 경로만 바꿔 통과
        └──── GET /api/map_tiles/naver_map_change/style/light.json
                                          → StyleView: MapLibre 래스터 스타일 JSON 생성
                                            (타일 URL = 우리 프록시 경로, 비밀값 없음)
    └ MapLibre 가 스타일의 tiles[] 를 요청
       transformRequest → withMapTilesToken() 이 ?token=<코어 회전토큰> 부착
        └──── GET /api/map_tiles/naver_map_change/12/3494/1602.png?token=...
                                          → TileView: 토큰 검증
                                            → 캐시 히트? 반환
                                            → 미스: provider URL 조립(버전코드 주입)
                                              + Referer/User-Agent 헤더로 upstream fetch
                                              → 캐시 저장 → 반환
```

## 2. 파일 구조

```
custom_components/naver_map_change/
├── __init__.py            setup / setup_entry / unload, 정적경로·JS주입 등록, 서비스 등록
├── config_flow.py         ConfigFlow + OptionsFlow
├── const.py               DOMAIN, 경로 상수, 캐시·타임아웃 한계값
├── providers.py           TileProvider 레지스트리 + 네이버 버전코드 갱신기
├── cache.py               인메모리 타일 캐시 (코어 map_tiles/cache.py 참조 구현)
├── view.py                TileView, StyleView
├── diagnostics.py         (선택) 진단 정보
├── manifest.json
├── services.yaml
├── translations/
│   ├── en.json
│   └── ko.json
└── frontend/
    └── naver-basemap.js   주입되는 프론트엔드 모듈 (~50줄)
```

도메인은 `naver_map_change` 를 유지한다 — 기존 저장소·HACS 등록명과 일치시키기 위함이다.
구 통합은 config entry 를 만들지 않았으므로 마이그레이션 코드는 불필요하다.

## 3. 모듈별 계약

### 3.1 `const.py`

```python
DOMAIN = "naver_map_change"

# 코어 map_tiles 네임스페이스에 얹는다 (근거: 02 문서 §3.3)
CORE_MAP_TILES_PATH = "/api/map_tiles"
URL_BASE = f"{CORE_MAP_TILES_PATH}/{DOMAIN}"
TILE_URL_PATH = f"{URL_BASE}/{{z}}/{{x}}/{{y}}.png"      # 뷰 등록용 (aiohttp 라우트)
STYLE_URL_PATH = f"{URL_BASE}/style/{{variant}}.json"

# 주입 JS
FRONTEND_URL_PATH = f"/{DOMAIN}_frontend"                 # 정적 서빙 경로
FRONTEND_SCRIPT = "naver-basemap.js"

# 프론트엔드가 가로챌 대상 (근거: 02 문서 §3.2 VECTOR_STYLES)
CORE_STYLE_PATHS = ("/static/map/light.json", "/static/map/dark.json")

# 코어 값과 정렬 (근거: 02 문서 §3.4 const.py)
UPSTREAM_TIMEOUT_S = 10
CACHE_MAX_BYTES = 32 * 1024 * 1024
MAX_FETCH_BYTES = 8 * 1024 * 1024
MAX_CONCURRENT_FETCHES = 16
TILE_TTL = 7 * 24 * 60 * 60
TILE_MAX_AGE = 7 * 24 * 60 * 60
MAX_COORDINATE_DIGITS = 8

CONF_PROVIDER = "provider"
CONF_API_KEY = "api_key"
CONF_URL_TEMPLATE = "url_template"
CONF_DARK_VARIANT = "dark_variant"
```

### 3.2 `providers.py`

```python
@dataclass(frozen=True, kw_only=True)
class TileProvider:
    id: str
    name: str
    url_template: str            # {z}/{x}/{y} + 선택적 {version}, {api_key}
    url_template_dark: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)   # Referer / User-Agent
    attribution: str             # MapLibre 스타일에 그대로 실린다 (표기 의무)
    min_zoom: int = 1
    max_zoom: int = 20
    max_native_zoom: int = 19    # 이 이상은 클라이언트가 업스케일
    tile_size: int = 256
    needs_api_key: bool = False
    version_refresher: str | None = None   # providers 내 갱신 함수 이름
```

**등록할 provider** (상세·리스크는 `04-BASEMAP-PROVIDERS.md`):

| id | 인증 | 비고 |
|---|---|---|
| `naver` | 없음(비공식) | **기본값.** `{version}` 자동 갱신 필요. `Referer: https://map.naver.com/` 헤더 필수 추정 |
| `vworld` | 무료 키 | `{api_key}` 필요. **URL 좌표 순서가 `{z}/{y}/{x}`** — 템플릿에 그대로 반영 |
| `osm` | 없음 | 검증용 대조군. 코어와 동일한 동작 확인에 사용 |
| `custom` | 사용자 입력 | `url_template` 을 사용자가 직접 입력 |

**네이버 버전코드 갱신기**

```python
async def async_refresh_naver_version(hass, session) -> str | None:
    """map.pstatic.net 의 스타일 메타에서 버전코드를 얻는다. 실패 시 None."""
```
- `https://map.pstatic.net/nrb/styles/basic.json` 을 `async_get_clientsession` 으로 GET.
- `version` 필드를 읽는다. 실패 시 정규식 폴백은 **넣지 않는다** (구 구현의 2단 폴백은 같은 URL을
  다시 때리는 것이라 의미가 없었다). 실패하면 `None` 을 반환하고 **마지막으로 성공한 값을 계속 쓴다.**
- `async_track_time_interval` 로 **6시간 주기** 갱신 + `entry` setup 시 1회.
- 갱신 실패가 지도 중단으로 이어지면 안 된다. 캐시된 버전이 없고 갱신도 실패하면
  `repairs` 이슈를 등록하고 스타일 엔드포인트는 **폴백 provider(`osm`)** 스타일을 반환한다.
- 버전이 바뀌면 **타일 캐시를 무효화**한다(캐시 키에 버전 포함이 더 간단하다 — 권장).

> ⚠️ **구현 시 실측 필요**: 네이버 타일의 `tile_size`(256 vs 512), `@2x` 접미사 유무,
> `maxNativeZoom`, 필요한 요청 헤더는 문서화된 스펙이 없다(`04` 문서 §2). 구현자는
> `curl` 로 실제 응답을 확인한 뒤 값을 확정하고, **확인한 curl 명령과 응답 헤더를 PR 에 남긴다.**
> 추측값을 코드에 넣지 않는다.

### 3.3 `cache.py`

코어 `homeassistant/components/map_tiles/cache.py` 의 축소 재구현.

- 인메모리 LRU, 총량 `CACHE_MAX_BYTES` 로 제한. **디스크에 쓰지 않는다** (HA는 SD카드에서 돌아간다).
- 키: `(provider_id, version, variant, z, x, y)`.
- 값: `body: bytes`, `content_type: str`, `encoding: str | None`, `expires: float`.
- 동일 키에 대한 동시 미스는 하나의 upstream 요청으로 합친다(in-flight dedup).
- 전역 동시 fetch 를 `asyncio.Semaphore(MAX_CONCURRENT_FETCHES)` 로 제한.
- **만료된 항목을 즉시 버리지 않는다** — upstream 장애 시 만료 캐시가 지도를 살려두는 유일한 수단이다
  (코어 `const.py` 주석이 같은 판단을 명시한다).

### 3.4 `view.py`

#### `NaverMapTileView`

```python
class NaverMapTileView(HomeAssistantView):
    url = "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"
    name = "api:naver_map_change:tile"
    requires_auth = False        # <img> 및 워커 요청은 Authorization 헤더를 못 붙인다
```

- **인증**: 코어 `_MapTilesView._authenticate` 를 그대로 모방한다.
  ```python
  from homeassistant.components.http import KEY_AUTHENTICATED
  # 코어 map_tiles 의 토큰 저장소를 재사용
  tokens = hass.data.get("map_tiles")          # HassKey("map_tiles") == DATA_ACCESS_TOKENS
  if request[KEY_AUTHENTICATED] or (tokens and request.query.get("token") in tokens):
      pass
  elif hdrs.AUTHORIZATION in request.headers:
      raise web.HTTPUnauthorized               # 실제 Bearer 시도 → ban 미들웨어 카운트
  else:
      raise web.HTTPForbidden                  # 만료 토큰 → IP 밴 방지
  ```
  `hass.data["map_tiles"]` 가 없으면(코어 통합 미로드) **토큰 검증을 건너뛰지 말고 403** 을 반환하고
  repairs 이슈를 등록한다. 열린 프록시가 되는 것이 더 나쁘다.
  > `DATA_ACCESS_TOKENS` 를 `homeassistant.components.map_tiles.const` 에서 import 해도 되지만,
  > 코어 내부 상수 의존을 줄이려면 문자열 `"map_tiles"` 를 자체 상수로 두고 `hass.data.get` 하는 편이
  > 낫다. 어느 쪽이든 **없을 때의 동작을 반드시 정의**한다.
- **좌표 검증**: 자릿수 `MAX_COORDINATE_DIGITS` 초과 즉시 거부(`int()` 비용 폭발 방지 — 코어와 동일),
  `z` 를 provider 범위로 클램프, `x/y` 를 `2**z` 범위로 검증. 벗어나면 404.
- **upstream fetch**: `async_get_clientsession(hass)`, `provider.headers`, `UPSTREAM_TIMEOUT_S`,
  `auto_decompress=False`, 64KB 청크로 읽으며 `MAX_FETCH_BYTES` 초과 시 거부.
  upstream 4xx/5xx → `502` 반환(캐시하지 않음).
- **응답 헤더**: `Cache-Control: private, max-age=TILE_MAX_AGE`, upstream `Content-Encoding` 승계.

#### `NaverMapStyleView`

```python
class NaverMapStyleView(HomeAssistantView):
    url = "/api/map_tiles/naver_map_change/style/{variant}.json"
    name = "api:naver_map_change:style"
    requires_auth = False        # loadStyle() 은 평범한 fetch 로 호출한다 (토큰 없음)
```

- `variant` 는 `light` | `dark` 만 허용, 그 외 404.
- **비밀값을 절대 담지 않는다.** 타일 URL 은 항상 **우리 프록시 경로**이며 API 키·upstream 도메인은
  응답에 나타나지 않는다. 이것이 `requires_auth = False` 를 안전하게 만드는 유일한 근거다.
- 반환 형식 (MapLibre Style Spec v8, 래스터 전용):
  ```json
  {
    "version": 8,
    "name": "naver_map_change",
    "sources": {
      "basemap": {
        "type": "raster",
        "tiles": ["/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"],
        "tileSize": 256,
        "minzoom": 1,
        "maxzoom": 19,
        "attribution": "© NAVER"
      }
    },
    "layers": [{ "id": "basemap", "type": "raster", "source": "basemap" }]
  }
  ```
  - `glyphs` / `sprite` 는 **넣지 않는다.** 래스터 전용 스타일에는 불필요하고,
    코어 `loadStyle()` 은 `sprite` 가 없으면 그대로 통과한다(02 문서 §3.2 검증).
  - `tiles[]` 는 루트 상대 경로로 둔다. `withMapTilesToken()` 이 `instanceOrigin()` 기준으로
    절대화하고 토큰을 붙인다 — Cast 환경까지 이 함수가 처리한다.
  - `attribution` 은 provider 값을 그대로 싣는다. **표기 의무를 코드 레벨에서 보장한다.**
  - `dark` 는 provider 에 `url_template_dark` 가 있으면 그 프록시 경로를, 없으면 light 와 동일한
    스타일을 반환한다(코어도 래스터에는 dark 변형이 없고 CSS 로 반전한다).

### 3.5 `frontend/naver-basemap.js`

**분량 50줄 이내. 로직을 넣지 않는다.** 스타일 결정은 전부 서버가 한다.

```js
// 코어가 가져오는 스타일 URL을 우리 엔드포인트로 갈아끼운다. 그 외에는 아무것도 하지 않는다.
(() => {
  const MAP = {
    "/static/map/light.json": "/api/map_tiles/naver_map_change/style/light.json",
    "/static/map/dark.json":  "/api/map_tiles/naver_map_change/style/dark.json",
  };
  const original = window.fetch;
  window.fetch = function (input, init) {
    try {
      const raw = typeof input === "string" ? input : input?.url;
      if (raw) {
        const path = new URL(raw, location.origin).pathname;
        const replacement = MAP[path];
        if (replacement) {
          return original.call(this, replacement, init);
        }
      }
    } catch (_err) {
      // 무엇이 잘못되든 원본 동작으로 흘려보낸다.
    }
    return original.apply(this, arguments);
  };
})();
```

요건:
- `window.fetch` 를 **한 번만** 래핑한다(모듈이 두 번 로드될 수 있으므로 중복 래핑 가드를 둔다).
- 우리 엔드포인트가 404/502 면 코어의 `glMap.on("error")` 복구 로직이 돌지만 지도는 비어 보인다.
  따라서 **StyleView 는 어떤 경우에도 유효한 스타일을 반환해야 한다** — provider 가 준비되지
  않았으면 `osm` 폴백 스타일을 반환한다(§3.2).
- Leaflet 래스터 폴백 경로(WebGL2 미지원 브라우저)는 **이 방식으로 덮이지 않는다.** §6.2 참조.

### 3.6 `__init__.py`

```python
async def async_setup(hass, config) -> bool:
    """서비스는 config entry 로드 여부와 무관하게 등록한다 (02 문서 §4.4)."""
    hass.services.async_register(DOMAIN, "refresh_version", ...,
                                 supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "clear_cache", ...,
                                 supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass, entry: NaverMapConfigEntry) -> bool:
    # 1) 런타임 데이터 구성 (hass.data[DOMAIN] 사용 금지 — 02 문서 §4.3)
    entry.runtime_data = NaverMapRuntimeData(provider=..., cache=..., version=None)

    # 2) 프론트엔드 JS 정적 서빙 (구 register_static_path 사용 금지)
    await hass.http.async_register_static_paths([
        StaticPathConfig(FRONTEND_URL_PATH,
                         str(Path(__file__).parent / "frontend"),
                         should_cache=False)
    ])

    # 3) 뷰 등록
    hass.http.register_view(NaverMapTileView(hass, entry))
    hass.http.register_view(NaverMapStyleView(hass, entry))

    # 4) 프론트엔드에 모듈 주입 — 버전 쿼리로 캐시 무효화
    add_extra_js_url(hass, f"{FRONTEND_URL_PATH}/{FRONTEND_SCRIPT}?v={INTEGRATION_VERSION}")

    # 5) 버전코드 초기 획득 + 6시간 주기 갱신
    entry.async_on_unload(async_track_time_interval(hass, _refresh, timedelta(hours=6)))

    # 6) 옵션 변경 리스너
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True
```

**unload 처리에 관한 정직한 서술** — 명세에 반드시 남길 것:
- `hass.http.register_view` / `async_register_static_paths` / `add_extra_js_url` 는
  **런타임 해제 API 가 없다.** 따라서 `async_unload_entry` 는 타이머·리스너·캐시만 정리하고,
  뷰와 주입 URL 은 **HA 재시작 시까지 남는다.**
- 구현은 이 사실을 숨기지 말고, 통합 삭제/재설정 시 사용자에게 "재시작이 필요하다"는
  `repairs` 이슈 또는 config flow 안내 문구를 남긴다.
- 뷰 핸들러는 entry 가 unload 된 상태를 감지하면 `503` 을 반환한다.

### 3.7 `config_flow.py`

- `single_config_entry: true` (manifest) → 중복 추가 차단.
- `async_step_user`:
  1. **HA 버전 가드** — `homeassistant.const.__version__` 이 2026.9.0 미만이면
     `async_abort(reason="unsupported_ha_version")`.
  2. provider 선택 (`selector.SelectSelector`, 기본 `naver`).
- `async_step_provider`: 선택한 provider 가 `needs_api_key` 면 키 입력,
  `custom` 이면 `url_template` 입력. 입력값은 **연결 테스트**로 검증한다
  (임의의 한 타일, 예: 서울 중심 z=12 타일을 실제로 받아 200/`image/*` 인지 확인).
  실패 시 `errors={"base": "cannot_connect"}`.
- `OptionsFlow`: provider·키·`dark_variant`·캐시 크기 변경. 변경 시 캐시 비우고 스타일 무효화.

### 3.8 `manifest.json`

```json
{
  "domain": "naver_map_change",
  "name": "Naver Map Change",
  "version": "2.0.0",
  "integration_type": "service",
  "config_flow": true,
  "single_config_entry": true,
  "iot_class": "cloud_polling",
  "dependencies": ["http", "frontend", "map_tiles"],
  "requirements": [],
  "documentation": "https://github.com/Leek13rl45/ha_naver_map_change",
  "issue_tracker": "https://github.com/Leek13rl45/ha_naver_map_change/issues",
  "codeowners": ["@Leek13rl45"]
}
```
- `dependencies` 에 `map_tiles` 를 넣는 이유: 우리 뷰가 그 토큰 저장소(`hass.data["map_tiles"]`)를
  읽으므로 **먼저 로드되어야 한다.**
  **[직접검증]** `map_tiles` 는 `frontend` 통합의 `dependencies` 목록에 포함되어 있다
  (`homeassistant/components/frontend/manifest.json`). 즉 프론트엔드가 로드되는 모든 설치에서
  항상 함께 로드된다 — `default_config` 에는 직접 나열되지 않지만 `frontend` 를 통해 보장된다.
  그래도 §3.4 의 "토큰 저장소가 없으면 403 + repairs 이슈" 경로는 **반드시 구현한다**
  (코어가 의존 관계를 바꿀 수 있고, 열린 프록시가 되는 것이 더 나쁘다).
- `iot_class` 는 upstream 을 주기적으로 호출하므로 `cloud_polling`. 구 `local_push` 는 오류.

### 3.9 `services.yaml` + `translations/`

- `apply` / `restore` 는 **제거**한다. 파일을 고치지 않으므로 개념 자체가 없다.
- 신규:
  - `refresh_version` — 버전코드 즉시 갱신. 응답 `{"version": "...", "changed": true|false}`.
  - `clear_cache` — 타일 캐시 비우기. 응답 `{"evicted_bytes": N}`.
- 서비스 이름·설명은 `translations/{en,ko}.json` 의 `services` 키에 둔다. `strings.json` 금지.
- `config` / `options` / `exceptions` 키도 두 언어 모두 채운다(`[%key:...%]` 참조 불가).

## 4. 수용 기준 (Acceptance Criteria)

구현이 끝났다고 보고하려면 **아래 전부를 실제 실행 로그와 함께** 제시해야 한다.

| # | 기준 | 검증 방법 |
|---|---|---|
| AC1 | HA 2026.9+ 에서 통합이 UI "통합구성요소 추가"에 나타나고 설정이 완료된다 | 스크린샷 또는 config entry 생성 로그 |
| AC2 | 지도 카드·`/map` 패널·person more-info **세 곳 모두** 베이스맵이 교체된다 | 각 화면 스크린샷 |
| AC3 | `site-packages` 아래 **어떤 파일도 변경되지 않는다** | 적용 전후 `find /usr/local/lib/python3.14/site-packages/hass_frontend -newer <ref>` 결과가 비어 있음 |
| AC4 | HA Core 를 업데이트해도 재적용 없이 동작한다 | 컨테이너 이미지 교체 후 재현 |
| AC5 | 타일 요청이 토큰 없이는 403 이다 | `curl -i .../12/3494/1602.png` → 403, `?token=<유효>` → 200 |
| AC6 | 스타일 엔드포인트 응답에 API 키·upstream 도메인이 없다 | `curl .../style/light.json` 출력 전문 |
| AC7 | 주입 JS 를 강제로 예외 발생시켜도 기본 OSM 지도가 정상 렌더된다 | JS 상단에 `throw` 삽입 후 스크린샷 |
| AC8 | provider 를 `osm` 으로 바꾸면 코어 기본 지도와 시각적으로 동일하다 | 대조 스크린샷 |
| AC9 | 이벤트 루프 blocking 경고가 로그에 없다 | `grep -i "blocking call" home-assistant.log` 결과 없음 |
| AC10 | `hassfest` 와 HACS validation 이 통과한다 | GitHub Actions 로그 |
| AC11 | 통합 삭제 후 재시작하면 기본 지도로 완전히 돌아온다 | 스크린샷 + `/api/map_tiles/naver_map_change/...` 404 확인 |
| AC12 | 네이버 버전코드가 바뀌어도 사용자 조작 없이 따라간다 | 캐시 키에 버전 포함을 보이는 단위 테스트 + 강제 변경 시나리오 |

## 5. 작업 분해 (구현 순서)

의존 관계상 이 순서를 지킨다. 각 단계 끝에서 검증하고 다음으로 넘어간다.

1. **정찰(코드 작성 없음)** — `curl` 로 네이버 타일 엔드포인트 실측:
   필요한 헤더, 실제 타일 크기, `@2x` 유무, 최대 줌, `basic.json` 응답 형태.
   **결과를 `docs/05-UPSTREAM-FINDINGS.md` 로 남긴다.** 이후 모든 상수는 이 문서를 근거로 한다.
2. **구 코드 삭제** — `__init__.py` 의 파일 패치 로직 전체 제거. 저장소를 빈 골격으로 만든다.
3. `const.py`, `providers.py` (`osm` provider 만 먼저) — 정답을 알고 있는 대조군으로 시작한다.
4. `cache.py` + `view.py`(TileView, StyleView) — provider `osm` 으로 **코어 기본 지도와 동일한
   결과**가 나오는지 먼저 확인(AC8 을 먼저 통과시킨다).
5. `frontend/naver-basemap.js` + `__init__.py` 의 정적경로·주입 배선. 여기서 AC2, AC7 확인.
6. `naver` provider + 버전코드 갱신기 추가. AC12 확인.
7. `config_flow.py` + `manifest.json` + `translations/`. AC1 확인.
8. `vworld`, `custom` provider 추가.
9. 서비스 2개(`refresh_version`, `clear_cache`) + `services.yaml`.
10. CI: `hassfest` + HACS validation 워크플로. README 전면 재작성. AC10 확인.
11. 전체 AC 재확인 후 태그·릴리스 생성(HACS 요건).

## 6. 알려진 한계와 완화책 — 명세에서 숨기지 않는다

### 6.1 벡터 → 래스터 하향
코어 2026.9 는 벡터 타일을 쓰지만 우리는 래스터를 넣는다. 결과:
- 지도 라벨이 회전/줌에 따라 재배치되지 않고, 확대 시 선명도가 떨어진다.
- 다크모드가 provider 의 dark 타일에 의존한다(없으면 라이트 타일 그대로).
- 대신 한국 지역 지도 품질(한글 라벨·도로·건물)은 크게 향상된다. **이 트레이드오프가 이 프로젝트의 존재 이유다.**

### 6.2 Leaflet 래스터 폴백 경로 미커버
WebGL2 미지원 브라우저는 `createRasterLayer()` 로 가고, 그 타일 URL은
`/api/map_tiles/raster/{z}/{x}/{y}.png` 로 고정되어 있다. 이 경로는 코어가 이미 라우트를
점유했으므로 우리가 응답할 수 없고, `<img>` 요청이라 `fetch` 후킹도 통하지 않는다.
- **1차 판단: 커버하지 않고 문서화한다.** 2026년 기준 WebGL2 미지원 브라우저는 소수다.
- 필요하면 선택적 확장: 주입 JS 가 `<ha-map>` 의 Leaflet 맵 인스턴스를 찾아 베이스 레이어를
  교체한다. **단 이는 문서화되지 않은 내부 구조 의존**이므로 `try/catch` 로 감싸고 실패를 무시한다.
  이 경로를 구현할 경우 별도 옵션(기본 off)으로 둔다.

### 6.3 코어 `map_tiles` 네임스페이스 결합
우리는 `/api/map_tiles/` 접두사와 `hass.data["map_tiles"]` 토큰 저장소에 의존한다.
코어가 이 둘 중 하나를 바꾸면 깨진다.
- 완화 1: 하위 경로명에 도메인을 써서(`naver_map_change`) 코어의 향후 하위 경로와 충돌 가능성을 낮춘다.
- 완화 2: 뷰 등록 시 토큰 저장소 존재를 확인하고, 없으면 **repairs 이슈를 등록하고 403 을 유지**한다.
  조용히 인증을 끄지 않는다.
- 완화 3: 자체 토큰 발급(WS 명령 + `add_extra_js_url` 로 넘기는 부트스트랩)으로 전환할 수 있게
  `view.py` 의 인증부를 한 함수로 격리해 둔다.
- **깨지는 방식이 안전하다**: 결합이 깨지면 타일이 403/404 → 코어 복구 로직 → 지도가 기본으로 보인다.
  구 방식처럼 번들이 손상되지 않는다.

### 6.4 약관·지속성
`naver` provider 는 문서화되지 않은 엔드포인트를 인증 없이 호출한다. 사용자 방침에 따라
1급 provider 로 지원하되, 다음을 코드·문서로 강제한다.
- README 와 config flow 설명에 **비공식 엔드포인트이며 예고 없이 차단될 수 있다**는 고지.
- `attribution` 표기를 스타일에 항상 포함.
- 캐시로 upstream 호출을 최소화(같은 타일 재요청을 7일 캐시).
- provider 교체가 UI 에서 즉시 가능하도록 유지 — 차단 시 사용자가 `vworld` 등으로 전환 가능.
상세 근거는 `04-BASEMAP-PROVIDERS.md`.

## 7. 하지 말 것 (구 구현에서 이식 금지 목록)

```
find_hass_frontend_dirs()   find_map_js_file()   patch_js_file()
restore_js_file()           recompress_js()      RETINA_PATTERNS
CARTO_TILE_PATTERN          get_naver_version()  # urllib 버전
apply / restore 서비스      *.js.bak 백업 개념   brotli / gzip import
sys.path 순회               python3.x 경로 하드코딩
```

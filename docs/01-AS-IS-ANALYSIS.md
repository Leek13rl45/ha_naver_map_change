# 01. AS-IS 분석 — ha_naver_map_change (커밋 a744298)

> 원본: https://github.com/Leek13rl45/ha_naver_map_change
> 이 문서는 재설계의 근거가 되는 **현재 구현의 사실 기록**이다. 코드를 직접 읽어 작성했다.

## 0. 결론 먼저 — 이 통합은 이미 동작하지 않는다

현재 구현은 프론트엔드 번들에서 문자열 `basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}` 를 찾아
치환하는 것이 동작의 전부다. 그런데 **HA 2026.9(2026-09-02 릴리스)에서 지도 베이스맵이 CARTO 래스터
타일에서 MapLibre GL + OSM 벡터 타일로 교체되면서, 그 문자열이 프론트엔드 번들에서 사라졌다.**

검증(직접 fetch): `home-assistant/frontend` dev 브랜치 `src/common/map/base-layer.ts` 에 CARTO 관련
문자열은 존재하지 않고, 대신 아래가 있다.

```ts
const VECTOR_STYLES = { light: "/static/map/light.json", dark: "/static/map/dark.json" } as const;
const RASTER_TILE_URL = `${MAP_TILES_PATH}/raster/{z}/{x}/{y}.png?token={token}`;
```

따라서 `apply` 서비스는 2026.9 이상에서 `_LOGGER.warning("교체할 패턴을 찾지 못했습니다")` 로 끝난다.
**이건 "낡아서 고쳐야 하는" 상태가 아니라 "기반 가정이 소멸해서 다시 설계해야 하는" 상태다.**
자세한 신규 구조는 `02-HA-PLATFORM-2026.md` 참조.

## 1. 저장소 전체 구조

```
.
├── .gitignore
├── hacs.json                                   (6줄)
├── README.md                                   (70줄)
└── custom_components/naver_map_change/
    ├── __init__.py                             (302줄)  ← 구현 전체
    ├── manifest.json                           (10줄)
    └── services.yaml                           (14줄)
```

- 파일 6개, 총 443줄. `config_flow.py`, `translations/`, 테스트, CI 워크플로 없음.
- 커밋 이력 10개 전부 `Add files via upload` / `Update __init__.py` — GitHub 웹 UI 직접 편집. 태그·릴리스 없음.

## 2. 동작 원리 (실제 코드 기준)

`naver_map_change.apply` 서비스 호출 시:

1. `get_naver_version()` — `urllib.request` 로 `https://map.pstatic.net/nrb/styles/basic.json` 을 받아
   `version` 필드 추출. 실패 시 쿼리스트링 붙은 같은 URL을 정규식으로 재시도, 그래도 실패하면 빈 문자열.
2. `find_hass_frontend_dirs()` — `import hass_frontend` 로 패키지 경로를 얻고, 실패하면
   `/usr/local/lib/python3.{14,13,12,11,10}/site-packages/hass_frontend` 순차 탐색, 그것도 실패하면
   `sys.path` 전체를 훑는다. 그 아래 `frontend_latest`, `frontend_es5` 두 디렉토리를 반환.
3. `find_map_js_file()` — 해당 디렉토리의 **모든 `.js` 파일을 열어 전문(全文)을 문자열로 읽고**,
   CARTO 타일 URL 문자열이 포함된 파일을 찾는다.
4. `patch_js_file()` — 찾은 minify 번들에서
   - CARTO 타일 URL 문자열을 네이버 타일 URL로 `str.replace`
   - retina 분기 코드를 **minify된 변수명 4가지 후보로 하드코딩 매칭**해 삭제:
     ```python
     RETINA_PATTERNS = [
         '+(t.Browser.retina?"@2x.png":".png")',
         '+(e.Browser.retina?"@2x.png":".png")',
         '+(n.Browser.retina?"@2x.png":".png")',
         '+(r.Browser.retina?"@2x.png":".png")',
     ]
     ```
   - `*.js.bak` 백업을 최초 1회 생성하고 원본 `.js` 를 덮어쓴다.
5. `recompress_js()` — 수정된 내용을 `brotli.compress` / `gzip.compress` 로 `*.js.br`, `*.js.gz` 에 재생성.
6. `persistent_notification` 으로 결과 알림. 사용자는 브라우저 강력 새로고침 필요.

`restore` 서비스는 `*.js.bak` 를 찾아 되돌리고 br/gz 를 다시 만든다.

**요약: 이 통합은 HA 프론트엔드 배포 산출물(파이썬 site-packages 안의 웹팩 번들)을 런타임에
자기 손으로 덮어쓰는 자기수정(self-modifying) 패처다.**

## 3. 확인된 결함 목록

### A. 아키텍처 결함 (재설계의 본질적 이유)

| # | 문제 | 상세 |
|---|---|---|
| A0 | **대상 문자열 소멸** | 위 0절. 2026.9+ 에서 치환 대상이 존재하지 않는다. |
| A1 | **설치 대상이 영속되지 않는 위치** | `site-packages/hass_frontend` 는 HA Core 컨테이너 이미지 레이어에 속한다. HA 업데이트는 컨테이너를 새 이미지로 교체하므로 수정이 사라진다. README도 이를 자인한다. |
| A2 | **minify 산출물 의존** | `RETINA_PATTERNS` 는 웹팩이 그때그때 배정한 변수명(`t`/`e`/`n`/`r`)을 찍어 맞추는 것이다. 프론트엔드 릴리스마다 깨질 수 있고, 실패해도 `str.replace` 가 no-op 이라 조용히 넘어간다. |
| A3 | **`.bak` 백업의 안전성 부재** | 백업은 "패치 성공 시 최초 1회"만 생성된다. 프론트엔드 번들 파일명에는 콘텐츠 해시가 들어가므로 HA 업데이트 후 `.bak` 는 짝을 잃은 고아 파일이 된다. |
| A4 | **br/gz 재압축 위험** | `brotli` 를 `manifest.json` 의 `requirements` 에 선언하지 않고 함수 안에서 `import brotli` 한다. 실패해도 `warning` 만 남기고 진행 → `.js` 와 `.js.br` 내용이 어긋난 상태가 만들어진다. 브라우저가 br 을 우선 받으면 패치가 적용 안 된 것처럼 보인다. |
| A5 | **전 프론트엔드 파일 전문 스캔** | `frontend_latest` 의 모든 `.js` 를 읽어 문자열 검색한다. 수십 MB 규모 I/O를 서비스 호출마다 수행. |
| A6 | **효과가 브라우저 캐시에 갇힘** | 파일명 해시가 그대로이므로 캐시 무효화가 안 되고, 사용자에게 수동 강력 새로고침을 요구한다. |
| A7 | **CORS 미고려** | 브라우저가 `map.pstatic.net` 을 직접 호출하는 구조다. Leaflet 래스터는 `<img>` 라 통과하지만, 2026.9 의 MapLibre 경로는 워커에서 `fetch` 로 타일을 받으므로 **CORS 헤더 없는 네이버 타일은 원천적으로 로드 불가**하다. |

### B. HA 통합 규격 위반

| # | 문제 | 상세 |
|---|---|---|
| B1 | **UI 추가 불가인데 README는 UI 추가를 안내** | `manifest.json` 에 `config_flow` 키가 없고 `config_flow.py` 도 없다. `async_setup(hass, config)` 만 구현되어 있어 `configuration.yaml` 에 도메인을 적어야 로드된다. README 의 "구성 → 통합구성요소 추가 → Naver Map Change" 는 **동작하지 않는 안내**다. |
| B2 | `manifest.json` 메타데이터 부실 | `documentation` 이 `https://github.com/your-repo/naver_map_change` 플레이스홀더. `codeowners` 빈 배열. `iot_class: local_push` 가 실제 성격과 불일치. `issue_tracker`(HACS 요구), `integration_type` 없음. |
| B3 | 이벤트 루프 블로킹 | `handle_restore_map` 안에서 **`os.listdir` 를 이벤트 루프에서 직접 호출**한다(`__init__.py:285`). HA 2024.7+ 의 blocking-call 탐지 대상. (`apply` 쪽 blocking 함수들은 `async_add_executor_job` 으로 올바르게 감쌌다.) |
| B4 | 번역 미분리 | `services.yaml` 에 한국어 하드코딩. `translations/` 없음. |
| B5 | 서비스 스키마·응답 없음 | `async_register` 에 `schema` 미지정, `SupportsResponse` 미사용 → 자동화가 성공·실패를 받을 방법이 없다. |
| B6 | HACS 요건 미충족 | 태그/릴리스 없음, `hassfest`·HACS validation 워크플로 없음, `hacs.json` 의 `homeassistant: "2024.1.0"` 방치. |
| B7 | `urllib` 사용 | executor 안이라 즉시 위반은 아니나, `async_get_clientsession()`(aiohttp) 전환이 공식 권장. |

### C. 약관·지속성 리스크

`map.pstatic.net/nrb/styles/...` 는 문서화되지 않은 내부 엔드포인트이며 인증키 없이 호출된다.
상세와 대안은 `04-BASEMAP-PROVIDERS.md`. **사용자 방침에 따라 재설계에서도 이 엔드포인트를 1급
provider 로 지원하되, provider 를 교체 가능한 구조로 만들어 리스크를 사용자 선택으로 넘긴다.**

## 4. 살릴 가치가 있는 것

- **문제 정의 자체는 유효하다** — 한국 사용자에게 HA 기본 지도(구 CARTO, 현 OSM)는 한글 라벨·도로
  정보가 빈약하다. 2026.9 의 OSM 전환으로 오히려 한국 지역 품질은 더 나빠졌다.
- 버전코드 자동 갱신, 백업/복원, 경로 자동 탐지의 **UX 의도**는 유지한다. 단 구현 위치를
  "파일 패치"에서 "정상 확장점"으로 옮기면 백업/복원 개념 자체가 불필요해진다.

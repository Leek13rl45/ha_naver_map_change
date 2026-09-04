# Naver Map Change for Home Assistant

[![hassfest](https://github.com/Leek13rl45/ha_naver_map_change/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/Leek13rl45/ha_naver_map_change/actions/workflows/hassfest.yaml)
[![HACS](https://github.com/Leek13rl45/ha_naver_map_change/actions/workflows/hacs.yaml/badge.svg)](https://github.com/Leek13rl45/ha_naver_map_change/actions/workflows/hacs.yaml)

Home Assistant 기본 지도의 **베이스맵(배경 지도)을 네이버 지도 등 한국 지도로 교체**하는 커스텀 통합구성요소입니다.

## 무엇을 하는 통합인가

Home Assistant 2026.9 부터 기본 지도는 OpenStreetMap 벡터 타일을 사용합니다. 이 지도는 한국 지역에서
한글 라벨이 드물고 도로·건물 정보가 빈약합니다. 이 통합은 지도 카드·`/map` 패널·사람(person) 상세 화면에
쓰이는 베이스맵을 네이버 지도 등의 한국 지도 타일로 바꿔 **한글 라벨과 도로 정보 품질을 끌어올립니다.**

지도에 표시되는 기기·사람 마커, 경로, 줌 조작 등 Home Assistant 지도의 나머지 동작은 그대로입니다.
바뀌는 것은 배경 지도 타일뿐입니다.

## 동작 원리

세 조각으로 동작합니다.

1. **타일 프록시 뷰** — Home Assistant 서버가 `/api/map_tiles/naver_map_change/{z}/{x}/{y}.png` 로
   타일을 서빙합니다. 서버가 제공자에게서 타일을 받아 메모리에 캐시하고 브라우저에 전달합니다.
   인증키는 서버에만 있고 브라우저로 나가지 않습니다.
2. **스타일 JSON 엔드포인트** — `/api/map_tiles/naver_map_change/style/{light|dark}.json` 에서
   MapLibre 래스터 스타일 JSON 을 서버가 생성합니다.
3. **주입 JS (약 30줄)** — Home Assistant 프론트엔드가 기본 스타일 파일을 가져갈 때 위 엔드포인트로
   경로만 갈아끼웁니다. 그 외에는 아무것도 하지 않으며, 어떤 단계에서 실패해도 기본 지도로 조용히
   되돌아갑니다.

### 구버전(1.x)과의 결정적 차이

구버전은 Home Assistant 프론트엔드의 압축된 JavaScript 번들 파일을 직접 덮어쓰는 방식이었습니다.
**이 버전은 Home Assistant 프론트엔드 파일을 전혀 수정하지 않습니다.** 통합에 필요한 모든 것이
`/config/custom_components/naver_map_change/` 안에 있으므로,

- **Home Assistant 를 업데이트해도 재적용이 필요 없습니다.**
- 실패해도 프론트엔드 번들이 손상되지 않습니다. 최악의 경우 기본 지도가 그대로 보입니다.
- 파일 쓰기 권한이나 설치 경로 탐지가 필요하지 않습니다.

## 요구 사항

- **Home Assistant 2026.9.0 이상.** 그 이전 버전은 지도 렌더링 구조(MapLibre 벡터 타일 + 스타일 JSON
  로딩 + 타일 토큰 체계)가 달라 이 설계 자체가 성립하지 않습니다. 설정 화면에서 버전을 확인하고
  미달이면 설정이 중단됩니다.
- WebGL2 를 지원하는 브라우저(2026년 기준 주요 브라우저 모두 해당).

## 설치

### HACS (Custom repository)

1. HACS → 우측 상단 메뉴 → **Custom repositories**
2. Repository: `https://github.com/Leek13rl45/ha_naver_map_change`, Category: **Integration**
3. 목록에서 **Naver Map Change** 를 선택해 다운로드

### 수동 설치

`custom_components/naver_map_change` 폴더를 Home Assistant 설정 디렉토리(`/config`)의
`custom_components/` 아래에 복사합니다.

### 설치 후

1. **Home Assistant 재시작**
2. **설정 → 기기 및 서비스 → 통합 구성요소 추가** → **Naver Map Change**
3. 사용할 지도 제공자(provider)를 선택합니다. 기본값은 `naver` 입니다.
   인증키가 필요한 제공자를 고르면 키 입력 화면이 이어집니다.

설정이 끝나면 지도 화면을 새로 열면 베이스맵이 교체되어 있습니다.
제공자와 인증 정보는 이후 통합 항목의 **설정(옵션)** 에서 언제든 바꿀 수 있습니다.

## 지도 제공자 (provider)

| id | 설명 | 인증 | 비고 |
|---|---|---|---|
| `naver` | 네이버 지도 타일 (`map.pstatic.net`) | 없음 | **기본값.** 문서화되지 않은 비공식 엔드포인트입니다. 타일 URL 에 들어가는 버전코드를 서버가 주기적으로 자동 갱신하므로 사용자가 손볼 것이 없습니다. 아래 [고지](#고지)를 반드시 읽으십시오 |
| `vworld` | 국토교통부 공간정보 오픈플랫폼 (VWorld) | 무료 인증키 필요 | 정식 인증 체계를 갖춘 공공 오픈플랫폼입니다. ⚠ **유효한 인증키로 검증하지 못한 상태입니다** — 좌표 순서, 사용 가능한 레이어명, 서버측 호출에 대한 도메인 등록 정책이 미확인입니다. 동작하지 않으면 `custom` 으로 URL 템플릿을 직접 넣어 우회하십시오 |
| `osm` | OpenStreetMap 표준 타일 | 없음 | Home Assistant 코어 기본 지도와 **동일한 소스**입니다. 대조군·폴백용이며, 이것을 고르면 한국 지역 품질 개선 효과는 없습니다 |
| `custom` | 사용자가 타일 URL 템플릿을 직접 입력 | 사용자 책임 | `{z}`/`{x}`/`{y}` 를 포함한 표준 XYZ 래스터 타일 URL. MapTiler 등 다른 제공자를 쓰려면 이 항목을 사용합니다 |

## 서비스(액션)

| 액션 | 설명 | 응답 |
|---|---|---|
| `naver_map_change.refresh_version` | 네이버 타일 버전코드를 즉시 갱신합니다(평시에는 서버가 주기적으로 자동 갱신합니다) | `{version, changed}` |
| `naver_map_change.clear_cache` | 서버측 타일 캐시를 비웁니다 | `{evicted_bytes}` |

```yaml
actions:
  - action: naver_map_change.refresh_version
    response_variable: result
  - action: system_log.write
    data:
      message: "naver map version={{ result.version }} changed={{ result.changed }}"
```

> **구버전에 있던 `naver_map_change.apply` / `naver_map_change.restore` 액션은 제거되었습니다.**
> 이 버전은 파일을 고치지 않으므로 "적용"과 "복원"이라는 개념 자체가 존재하지 않습니다.
> 통합을 추가하면 바로 적용되고, 통합을 삭제하면 되돌아갑니다.

## 알려진 한계

숨기지 않고 적습니다.

- **벡터 지도 → 래스터 지도 하향.** Home Assistant 2026.9 의 기본 지도는 벡터 타일이지만 이 통합은
  래스터(이미지) 타일을 넣습니다. 그래서 지도 라벨이 회전·줌에 따라 재배치되지 않고, 크게 확대하면
  선명도가 떨어집니다. 그 대가로 한국 지역의 한글 라벨·도로·건물 품질이 크게 향상됩니다.
  **이 트레이드오프가 이 프로젝트가 존재하는 이유입니다.**
- **다크 모드.** `naver` 제공자에는 어두운 계열 타일이 존재하지 않습니다(`basic`/`satellite`/`terrain`
  세 계열만 제공됨). 따라서 다크 테마에서도 밝은 타일이 그대로 표시됩니다.
- **WebGL2 미지원 브라우저는 커버하지 않습니다.** 그런 브라우저에서 Home Assistant 는 Leaflet 래스터
  경로로 폴백하는데, 그 경로의 타일 URL 은 코어가 고정으로 점유하고 있어 이 통합이 개입할 수 없습니다.
  해당 환경에서는 기본 지도가 그대로 보입니다.
- **통합을 삭제하거나 다시 설정하면 Home Assistant 재시작이 필요합니다.** 이 통합이 등록하는 HTTP 뷰와
  프론트엔드 스크립트 주입은 Home Assistant 에 런타임 해제 API 가 없기 때문입니다. 삭제 직후에도
  재시작 전까지는 뷰가 남아 있습니다.
- **코어의 `map_tiles` URL 네임스페이스에 의존합니다.** 이 통합은 타일 인증 토큰을 직접 발급하지 않고
  코어 `map_tiles` 통합의 회전 토큰 체계를 재사용합니다. 코어가 이 구조를 바꾸면 타일 요청이 403/404 가
  되고, 그때 **지도는 기본 지도로 돌아갑니다.** 지도가 백지가 되지는 않습니다.

## 고지

**읽고 나서 사용을 결정하십시오.**

- `naver` 제공자는 `map.pstatic.net` 의 **문서화되지 않은 비공식 엔드포인트**를 인증 없이 호출합니다.
  네이버 클라우드 플랫폼의 정식 상품군에는 `z/x/y` 래스터 타일을 서빙하는 API 가 없습니다(과거의
  Tile Map API 는 2020-06-01 종료). 즉 이 경로는 계약·쿼터 체계 밖이며, **예고 없이 URL 스킴이 바뀌거나
  차단될 수 있습니다.**
- 네이버지도의 법적 고지는 해당 지도가 "측량·수로조사 및 지적에 관한 법률"에 의거하여
  국토교통부장관의 사전승인 없이는 **복제, 국외 반출 및 본지도를 이용한 다른 지도의 간행을 금지**한다고
  명시하고 있습니다 (<https://ssl.pstatic.net/static/maps/mantle/notice/legal.html>).
  이 조항이 정식 키 없는 타일 직접 호출까지 포섭하는지에 대한 개별 판례·유권해석은 확인되지 않았습니다.
- 이 통합은 **개인 사용 범위를 전제**하며, 타일을 파일로 저장하거나 내보내는 기능을 제공하지 않습니다.
  타일은 서버 메모리에만 캐시되고 디스크에 기록되지 않습니다. 제공자 표기(attribution)는 지도 스타일에
  항상 포함됩니다. 그럼에도 **사용 판단과 그에 따른 책임은 사용자에게 있습니다.**
- 차단되거나 방침이 맞지 않으면, 통합 설정 화면에서 `vworld` 등 다른 제공자로 즉시 교체할 수 있습니다.
  제공자를 바꿔도 재설치나 파일 수정은 필요하지 않습니다.

## 1.x → 2.0 업그레이드 안내

구버전(1.x)은 Home Assistant 프론트엔드가 설치된 `site-packages` 경로의 `.js` 파일을 직접 덮어쓰고
`*.js.bak` 백업 파일을 남겼습니다. 2.0 은 그 방식을 완전히 폐기했습니다.

- **구버전을 사용한 적이 있다면, Home Assistant Core 를 업데이트하거나 재설치해 프론트엔드를 원상
  복구하는 것이 가장 확실합니다.** 프론트엔드 번들 파일명에는 콘텐츠 해시가 들어가므로, 구버전이 만든
  `*.js.bak` 파일은 Home Assistant 를 업데이트한 뒤에는 짝을 잃은 고아 파일이 되어 복원에 쓸 수 없습니다.
- **2.0 은 그 파일들을 더 이상 건드리지 않으며, 남아 있는 `.bak` 파일을 정리해주지도 않습니다.**
  덮어쓰기·백업·복원 코드가 저장소에서 삭제되었기 때문입니다. 남은 파일이 신경 쓰이면 위와 같이
  Core 를 업데이트/재설치하십시오.
- 구버전은 config entry 를 만들지 않았으므로 설정 마이그레이션은 없습니다. 2.0 을 설치하고 재시작한 뒤
  통합 구성요소를 새로 추가하면 됩니다.

## 문서

설계 배경과 실측 근거는 `docs/` 폴더에 있습니다.

| 문서 | 내용 |
|---|---|
| `docs/01-AS-IS-ANALYSIS.md` | 구버전 구현이 무엇을 했고 왜 못 쓰는지 |
| `docs/02-HA-PLATFORM-2026.md` | Home Assistant 2026.9 지도 구조·통합 규격 |
| `docs/03-REDESIGN-SPEC.md` | 재설계 명세와 알려진 한계 |
| `docs/04-BASEMAP-PROVIDERS.md` | 지도 제공자 선택 근거와 약관 리스크 |
| `docs/05-UPSTREAM-FINDINGS.md` | 타일 엔드포인트 실측 결과(`curl` 로그 포함) |

## 저작자

- 저장소: <https://github.com/Leek13rl45/ha_naver_map_change>
- 이슈: <https://github.com/Leek13rl45/ha_naver_map_change/issues>
- 코드오너: [@Leek13rl45](https://github.com/Leek13rl45)

이 저장소는 [MIT License](LICENSE) 로 배포됩니다.

지도 타일 자체는 이 라이선스의 대상이 아닙니다. 타일의 저작권과 이용 조건은 각 제공자에게 있으며,
특히 `naver` 제공자에 대해서는 위 [고지](#고지) 를 반드시 확인하십시오.

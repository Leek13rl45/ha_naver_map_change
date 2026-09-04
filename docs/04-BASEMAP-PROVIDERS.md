# 04. 타일 제공자(basemap provider) 조사와 선택

> 조사 시점 2026-09-04. **[조사]** = 하청 에이전트 웹 근거 기반. **[미확인]** = 근거 부족.
> **사용자 방침: 공식 공개 API 로 제한하지 않는다. 비공식 엔드포인트를 포함해 사용 가능한 것을 쓴다.**
> 이 문서는 그 방침 아래에서 **무엇을 쓰고, 무엇을 알고 쓰는지**를 기록한다.

---

## 1. NAVER Cloud Platform 정식 상품 — 타일 API는 없다 [조사]

- 현재 NCP Maps 상품군: Web Dynamic Map(JS SDK), Mobile Dynamic Map SDK, **Static Map**,
  Directions 5/15, Geocoding, Reverse Geocoding.
- **`z/x/y` 래스터 타일을 직접 서빙하는 정식 상품이 없다.** 과거의 **Tile Map API 는 2020-06-01 서비스
  종료**되어 호출 불가.
  → https://docs.ncloud.com/en/naveropenapi_v3/maps/tile/tile-map.html
- Static Map 은 **좌표 중심의 이미지 1장**을 반환하는 API 로, Leaflet/MapLibre 의 타일 레이어에
  직결할 수 없다. 인증은 `x-ncp-apigw-api-key-id`/`x-ncp-apigw-api-key` 헤더 또는 Referer 등록 방식.
  → https://api.ncloud-docs.com/docs/en/ai-naver-mapsstaticmap-raster
- 무료 쿼터·요금표 세부는 **[미확인]** (콘솔 요금계산기 확인 필요).

**결론**: "정식 네이버 API 로 HA 베이스맵을 만든다"는 경로는 **존재하지 않는다.** 정식 상품을 쓰려면
지도 렌더링 자체를 네이버 JS SDK 로 바꿔야 하고, 그건 HA 프론트엔드 교체 수준의 작업이다.
따라서 네이버 지도를 쓰려면 비공식 타일 엔드포인트가 유일한 선택지다 — 사용자 방침이 이를 허용한다.

## 2. `map.pstatic.net/nrb/styles/...` — 비공식 내부 엔드포인트 [조사]

- NCP 공식 레퍼런스·개발자 가이드 어디에도 `nrb/styles/basic.json` 이나
  `{version}/{z}/{x}/{y}` 패턴이 문서화되어 있지 않다. 네이버 지도 웹 서비스가 내부적으로 쓰는
  타일 서버로 판단된다.
- 같은 방식이 HA 생태계에서 이미 쓰이고 있다: `nathan-gs/ha-map-card` 의
  [issue #94](https://github.com/nathan-gs/ha-map-card/issues/94) 가 정확히 "네이버 지도의 버전
  코드가 주기적으로 바뀌어 `tile_layer_url` 이 깨진다 → 템플릿 지원이 필요하다"는 요청이다.
  **버전코드 자동 갱신은 이 프로젝트가 그 생태계에 제공할 수 있는 실질적 차별점이다.**
- 공식 쿼터·계약 체계 밖이므로 **예고 없이 URL 스킴 변경·차단이 가능**하다.

### 구현 전 실측해야 할 것 (추측 금지)

문서화된 스펙이 없으므로 아래는 **전부 `curl` 실측으로 확정**한다. 결과는
`docs/05-UPSTREAM-FINDINGS.md` 에 명령어와 응답 헤더째로 남긴다.

| 항목 | 왜 필요한가 |
|---|---|
| 필수 요청 헤더 (`Referer`, `User-Agent`) | 없으면 403 일 가능성. 코어 프록시도 같은 이유로 헤더를 붙인다 |
| 실제 타일 픽셀 크기 (256 / 512) | MapLibre 래스터 소스의 `tileSize` 값이 여기서 결정된다 |
| `@2x` 접미사의 유효성·필요성 | 2026.9 코어는 `@2x` 분기를 삭제했다("OSM serves no @2x variant") |
| 최대 줌 레벨 | `maxzoom` / `max_native_zoom` 값 |
| 좌표계가 표준 Web Mercator(EPSG:3857) z/x/y 인지 | 아니면 오프셋 보정이 필요하다. **[미확인]** — 공식 스펙 없음 |
| `basic.json` 의 응답 구조와 `version` 필드 위치 | 갱신기 파싱 대상 |
| 응답 `Cache-Control` | 캐시 TTL 을 upstream 에 맞출 수 있는지 |

> ⚠️ 네이버 JS SDK(`naver.maps.Tile`)가 표준 슬리피맵 z/x/y 개념을 쓴다는 정황은 있으나,
> `map.pstatic.net/nrb/...` 엔드포인트 자체의 CRS·타일크기·최대줌에 대한 **공식 스펙 문서는 없다.**
> 우연히 Leaflet 템플릿에 맞더라도 보증된 스키마가 아니다.

## 3. 약관·법령 리스크 — 알고 쓴다 [조사]

사용자 방침이 비공식 API 사용을 허용했으므로 **차단 사유로 삼지 않는다.** 다만 아래는 사실이므로
README 와 config flow 설명에 고지한다.

- 네이버지도 법적 고지: 지도는 "측량·수로조사 및 지적에 관한 법률"에 의거 국토교통부장관의 사전승인
  없이는 **복제, 국외 반출 및 본지도를 이용한 다른 지도의 간행을 금지**한다고 명시하며, 위반 시
  형사처벌 조항(제16·21조 위반 2년 이하 징역 또는 2천만원 이하 벌금 등)을 든다.
  → https://ssl.pstatic.net/static/maps/mantle/notice/legal.html
- OSM 한국 커뮤니티도 같은 법령을 근거로 국내 서버 지도 데이터를 가져와 베끼는 행위를 경고한다.
  → https://community.openstreetmap.org/t/topic/5549
- 이 조항이 "정식 키 없는 타일 직접 호출"까지 포섭하는지에 대한 개별 판례·유권해석은 **[미확인]**.

**설계에 반영하는 완화책** (`03-REDESIGN-SPEC.md` §6.4):
개인 사용 범위, 7일 타일 캐시로 호출 최소화, `attribution` 상시 표기, provider 즉시 교체 가능,
재배포·재간행 기능 없음(타일을 저장·내보내는 기능을 만들지 않는다).

## 4. 대안 provider

### 4.1 VWorld (국토교통부 공간정보 오픈플랫폼) — 권장 2순위 [조사]

```
https://api.vworld.kr/req/wmts/1.0.0/{apikey}/Base/{z}/{y}/{x}.png
```
- **표준 WMTS z/x/y 타일을 제공하는, 인증이 정식인 유일한 한국 지도 후보.**
- 무료(공공데이터). 회원가입 후 인증키 발급, 사이트 URL/도메인 등록 필요.
- ⚠️ **좌표 순서가 `{z}/{y}/{x}`** — 일반적인 `{z}/{x}/{y}` 와 다르다. 템플릿에 그대로 반영해야 한다.
- 제약: `domain` 파라미터 검증 정책, 일일 요청 제한(초과 시 별도 승인), 원칙상 외부망 서비스 전제.
  → https://www.vworld.kr/dev/v4dv_apiuse_s001.do
- 스타일 계열: `Base`, `gray`, `midnight`(다크에 활용 가능), `Satellite` 등이 있다고 알려져 있으나
  이 프로젝트에서 사용할 정확한 레이어명은 **[미확인]** — 실측 확인 대상.

### 4.2 Kakao Map — 부적합 [조사]
JS SDK 종속. 커스텀 Tileset 은 SDK 내부 `getTile(x,y,z)` 콜백으로만 동작하고, Leaflet/MapLibre 에
직결 가능한 표준 z/x/y raw 타일 URL 은 공식 문서에서 확인되지 않는다. 약관 원문도 **[미확인]**.
→ **provider 목록에 넣지 않는다.**

### 4.3 OSM 계열
- `https://tile.openstreetmap.org/{z}/{x}/{y}.png` — 코어가 프록시로 쓰는 것과 같은 소스.
  **대조군(`osm` provider)으로 반드시 등록한다** — 우리 프록시·스타일·주입 경로가 올바른지
  검증하는 기준선이 되기 때문이다(`03` 문서 AC8).
- 한국어 라벨은 제한적이며, OSMF Tile Usage Policy 가 대량·자동화 트래픽을 제한한다.
- `tiles.osm.kr`("군사시설 없는 한국 OSM 타일")이 존재하나 URL 템플릿·라이선스·attribution 조건이
  확인되지 않음 **[미확인]**.
- MapTiler / Mapbox 의 OSM 기반 한국어 스타일은 표준 XYZ 타일을 공식 제공하나 API 키(무료 티어
  요청 수 제한)가 필요하다. `custom` provider 로 사용자가 직접 넣으면 된다.

## 5. 최종 provider 결정

| id | URL 템플릿 | 인증 | 약관 안전성 | 채택 |
|---|---|---|---|---|
| `naver` | `map.pstatic.net/nrb/styles/basic/{version}/{z}/{x}/{y}...` (실측 확정) | 없음(비공식) | 낮음 — 복제·간행 금지 조항 존재 | ✅ **기본값** (사용자 방침) |
| `vworld` | `api.vworld.kr/req/wmts/1.0.0/{api_key}/Base/{z}/{y}/{x}.png` | 무료 키 + 도메인 등록 | 높음(공공 오픈플랫폼) | ✅ 2순위 |
| `osm` | `tile.openstreetmap.org/{z}/{x}/{y}.png` | 없음 | 중간(ODbL, 정책 준수 시) | ✅ 대조군·폴백 |
| `custom` | 사용자 입력 | 사용자 책임 | — | ✅ (MapTiler 등 우회로) |
| NCP Static Map | — | 키 필요 | 높음 | ❌ 타일 API 아님 |
| Kakao | — | SDK 키 | 불확실 | ❌ Leaflet 직결 불가 |

## 6. Leaflet / MapLibre 래스터 소스 옵션 체크리스트

두 엔진 모두 아래를 provider 메타데이터에서 채워야 한다.

| 옵션 | 의미 | 주의 |
|---|---|---|
| `attribution` | 제공자 표기 | **의무.** 스타일 JSON 에 항상 포함(`03` 문서 §3.4) |
| `tileSize` | 타일 픽셀 크기 | 256 이 표준. 512 타일이면 Leaflet 은 `zoomOffset:-1` 조합 필요. MapLibre 는 `tileSize` 만 맞추면 됨. **실측 확정 대상** |
| `minzoom` / `maxzoom` | 제공 줌 범위 | 코어 기준 `MAP_MIN_ZOOM=1`, `MAP_MAX_ZOOM=20`, 래스터 native 상한 19 |
| `maxNativeZoom` | 이 이상은 클라이언트 업스케일 | 없는 타일을 요청해 404 도배하는 것을 막는다 |
| 좌표 순서 | `{z}/{x}/{y}` vs `{z}/{y}/{x}` | **VWorld 는 y/x 순서** |
| `subdomains` | `{s}` 분산 | 해당 provider 만 |

## 7. 요약

- 네이버 지도를 HA 베이스맵으로 쓰는 **정식 경로는 존재하지 않는다.** 비공식 타일 엔드포인트가
  유일하며, 사용자 방침에 따라 이를 기본 provider 로 채택한다.
- 대신 설계는 **provider 교체 가능성**을 1급 요구사항으로 둔다. 네이버가 차단되면 사용자가 UI 에서
  `vworld` 로 바꾸면 끝나야 한다. 이것이 "비공식 API 를 쓰면서도 프로젝트가 죽지 않는" 유일한 구조다.
- 버전코드 자동 갱신은 기존 생태계(`ha-map-card` #94)가 못 하고 있는 지점이며,
  **서버측에서 처리하면 파일 패치 없이 깔끔하게 해결된다.**

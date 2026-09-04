# 05. Upstream 실측 결과 (UPSTREAM FINDINGS)

> 측정 시점: 2026-09-04 13:24~13:26 UTC. 측정 위치: 한국(KR) 일반 회선, macOS `curl`.
> `03-REDESIGN-SPEC.md` §5-1 의 정찰 단계 산출물이다. **이 문서 이후의 모든 상수는 여기를 근거로 한다.**
> 표기: **[실측]** = 아래 명령을 실행해 응답을 직접 확인함. **[미확인]** = 실측하지 못함.

---

## 0. 요약 — 명세와 달라진 것 (먼저 읽는다)

| # | `03`/`04` 문서의 가정 | 실측 결과 | 영향 |
|---|---|---|---|
| F1 | 타일 포맷 `.png` | **기본은 `.jpg`.** `.png` 도 응답하지만 동일 타일이 16KB→26KB 로 커진다 | ~~`.jpg` 를 쓴다(v2.0.1)~~ → **정정(v2.1.0, D13): `.png` 를 쓴다.** 아래 §3 정정 참조. 프록시는 upstream `Content-Type` 을 그대로 승계 |
| F2 | `Referer` / `User-Agent` 헤더 필수 추정 | **헤더 없이 200.** 브라우저 UA/Referer 를 붙여도 동일하게 200 | `provider.headers` 는 빈 dict 로 시작. 구조는 유지(차단 시 대응 여지) |
| F3 | CORS 헤더 없음 → 브라우저 직접 호출 불가 | **`access-control-allow-origin: *` 가 있다.** 타일·tilejson 모두 | **프록시의 근거가 바뀐다.** §6 참조 — 설계는 유지되지만 이유가 다르다 |
| F4 | `max_native_zoom` 19 (코어 값) | tilejson `maxzoom: 21`, z21 에서 실제 타일 확인 | naver provider `max_native_zoom = 21` |
| F5 | 좌표 범위 초과 시 404 예상 | **200 + 빈 타일**을 반환한다 | 좌표 검증을 프록시가 직접 해야 한다(upstream 이 걸러주지 않는다) |
| F6 | 버전코드 만료 동작 미상 | 잘못된 version → **HTTP 400, 본문 0바이트** | 400 을 "버전 만료" 신호로 쓴다. 즉시 갱신 트리거 후보 |
| F7 | `basic.json` 구조 미상 | **TileJSON 2.1.0.** `version` 필드 + `tiles[]` 템플릿까지 들어 있다 | 갱신기가 URL 템플릿까지 신뢰 가능. `scheme: "xyz"` 로 표준 확인 |
| F8 | 다크 타일 유무 미상 | `basic`/`satellite`/`terrain` 만 존재. **dark 계열 없음** | naver 는 `url_template_dark = None` → light 스타일 재사용 (`03` §3.4 규정대로) |

---

## 1. 스타일 메타(tilejson) — 버전코드의 출처

```console
$ curl -s -i -m 15 "https://map.pstatic.net/nrb/styles/basic.json"
HTTP/2 200
server: Testa/6.2.14
date: Fri, 04 Sep 2026 13:24:03 GMT
content-type: application/json
content-length: 298
last-modified: Thu, 03 Sep 2026 05:37:03 GMT
cache-control: max-age=300
access-control-allow-origin: *
age: 0
strict-transport-security: max-age=31536000

{"tilejson":"2.1.0","name":"","attribution":"","scheme":"xyz","minzoom":0,"maxzoom":21,
 "version":"1787907321","bounds":[-180.0,-85.051128779807,180.0,85.051128779807],
 "format":"jpg","center":[127.929498,36.607695,7.0],
 "tiles":["https://map.pstatic.net/nrb/styles/basic/1787907321/{z}/{x}/{y}.jpg"]}
```

**[실측] 확정 사항**

| 항목 | 값 | 비고 |
|---|---|---|
| 응답 규격 | TileJSON 2.1.0 | 임의 JSON 이 아니라 표준 스펙이다 |
| `version` 필드 | `"1787907321"` (문자열) | 갱신기 파싱 대상. 10자리 숫자 문자열 |
| `scheme` | `"xyz"` | **표준 Web Mercator(EPSG:3857) z/x/y 확정.** 오프셋 보정 불필요 — `04` 문서 §2 의 [미확인] 항목 해소 |
| `minzoom` / `maxzoom` | 0 / 21 | 코어 `MAP_MIN_ZOOM=1`, `MAP_MAX_ZOOM=20` 보다 넓다 |
| `format` | `"jpg"` | → F1 |
| `attribution` | `""` (빈 문자열) | **upstream 이 표기 문구를 주지 않는다.** 우리가 `"© NAVER"` 를 직접 넣어야 한다 |
| `tiles[0]` | `.../basic/{version}/{z}/{x}/{y}.jpg` | URL 템플릿 확정 |
| `cache-control` | `max-age=300` | 메타는 5분 캐시. **6시간 주기 갱신(`03` §3.2)은 upstream 정책보다 보수적이므로 안전** |
| `last-modified` | 2026-09-03 05:37 UTC | 측정 하루 전. 버전 회전 주기는 **[미확인]**(단발 측정으로는 알 수 없음) |

### 존재하는 스타일 계열

```console
$ for s in basic satellite terrain hybrid dark night gray light basic_ko vector; do
    curl -s -o /dev/null -w "$s %{http_code} %{content_type} %{size_download}\n" \
      -m 12 "https://map.pstatic.net/nrb/styles/$s.json"; done
basic        200 application/json 298
satellite    200 application/json 302
terrain      200 application/json 300
hybrid       404  0
dark         404  0
night        404  0
gray         404  0
light        404  0
basic_ko     404  0
vector       404  0
```

`satellite` / `terrain` 도 **동일한 version 값(`1787907321`)과 동일한 tilejson 구조**를 쓴다.
→ 버전코드는 스타일별이 아니라 **전역**이다. 갱신기 하나로 세 스타일을 모두 커버한다.

> **설계 반영**: naver provider 를 `style` 파라미터(`basic`|`satellite`|`terrain`)로 확장할 수 있다.
> `03` 명세 범위 밖이므로 **1차 구현에서는 `basic` 고정**하되, URL 템플릿에 스타일명을
> 하드코딩하지 말고 provider 정의로 분리해 둔다.

---

## 2. 타일 응답 — 헤더 없이 200

```console
$ curl -s -o t12.jpg -D - -m 15 \
    "https://map.pstatic.net/nrb/styles/basic/1787907321/12/3492/1586.jpg"
HTTP/2 200
server: Testa/6.2.14
content-type: image/jpeg
content-length: 16360
last-modified: Mon, 20 Jul 2026 08:31:17 GMT
cache-control: max-age=31536000
etag: "0b44b1d3e88d5bf9058724e6589e55ebe"
access-control-allow-origin: *
age: 504445
strict-transport-security: max-age=31536000

$ file t12.jpg
t12.jpg: JPEG image data, JFIF standard 1.01, ..., baseline, precision 8, 256x256, components 3
```

(z=12, x=3492, y=1586 = 서울 시청/경복궁 일대. `lat 37.5665, lon 126.9780` 를 표준 슬리피맵 공식으로 변환.)

**[실측] 확정 사항**

| 항목 | 값 | 코드 반영 |
|---|---|---|
| 인증 | **없음.** 헤더 0개로 200 | `provider.headers = {}` |
| `tile_size` | **256** (`file` 출력의 `256x256`) | `tileSize: 256` |
| `content-type` | `image/jpeg` | 프록시가 그대로 승계 |
| `cache-control` | `max-age=31536000` (1년) | URL 에 version 이 들어가므로 **사실상 immutable**. `TILE_TTL` 7일은 보수적이라 안전 |
| `etag` | 있음 | 조건부 요청 가능 **[미확인]**(`If-None-Match` 동작은 실측 안 함) |
| CORS | `access-control-allow-origin: *` | → F3 |

### 헤더를 붙여도 결과가 같다

```console
$ curl -s -m 15 -H "Referer: https://map.naver.com/" -H "User-Agent: Mozilla/5.0" \
    -o /dev/null -w "%{http_code} %{content_type}\n" \
    "https://map.pstatic.net/nrb/styles/basic/1787907321/12/3492/1586.jpg"
200 image/jpeg
```

→ `Referer` 게이트가 **없다**. `04` 문서 §2 의 "없으면 403 일 가능성"은 실측으로 부정됐다.
단 provider 의 `headers` 필드는 **삭제하지 않는다** — 네이버가 나중에 게이트를 걸면 코드 변경 없이
provider 정의만 고쳐 대응할 수 있어야 한다(`04` §7 의 "죽지 않는 구조").

---

## 3. 포맷·해상도 변형

```console
$ curl -s -o /dev/null -w "%{http_code} %{content_type} %{size_download}\n" ...
  .../1787907321/12/3492/1586.jpg      200 image/jpeg 16360      # 256x256
  .../1787907321/12/3492/1586.png      200 image/png  26510      # 256x256
  .../1787907321/12/3492/1586@2x.jpg   200 image/jpeg 52542      # 512x512
```

```console
$ file -b x2.jpg
JPEG image data, ..., 512x512, components 3
```

**[실측]**
- `.png` 도 유효하지만 **같은 타일이 1.62배 크다.** upstream 부하·캐시 효율 모두 불리하다.
- `@2x` 는 유효하며 실제로 **512x512** 를 반환한다(3.2배 용량).
- 코어 2026.9 는 `@2x` 분기를 삭제했다(`02` §3.2 — "OSM serves no @2x variant"). 네이버는 지원하지만
  **1차 구현에서는 쓰지 않는다.** 도입하려면 `tileSize: 512` 와 세트로 가야 하고, 32MB 인메모리 캐시에
  담기는 타일 수가 1/3로 줄어든다.

> **결정 (v2.0.1 — 아래에서 뒤집힘)**: upstream 은 `.jpg` 256px 를 쓴다. 우리 라우트는 `03` §3.4 대로
> `.../{z}/{x}/{y}.png` 를 유지한다(코어 URL 형태와의 일관성). **확장자와 실제 바이트가 불일치하지만
> 무해하다** — MapLibre 와 Leaflet 은 모두 `Content-Type` 을 보고 디코딩하며 확장자를 신뢰하지 않는다.
> 프록시가 upstream `Content-Type: image/jpeg` 를 그대로 승계하므로 브라우저에서 정상 렌더된다.
> 이 결정을 코드 주석에 근거와 함께 남긴다.

---

### ⚠️ 정정 (v2.1.0) — 위 §3 의 결정 두 개가 뒤집혔다

이 문서는 근거 기록이므로 위 원문을 지우지 않는다. 아래가 현재 유효한 결정이다.

**정정 계기**: v2.0.1 릴리스 후 실사용자(macOS, Retina `devicePixelRatio` = 2)가
**"화질이 많이 깨진다"**, **"버스정류장 같은 게 안 뜬다"** 고 보고했다. 원인이 셋이었고 그 중 둘이 위
§3 에서 내린 결정이다. 1.x 구현(`v1.3.0` 태그의 `custom_components/naver_map_change/__init__.py`,
`build_naver_tile_url()`)은 세 개를 모두 쓰고 있었다:

```
https://map.pstatic.net/nrb/styles/basic/{version}/{z}/{x}/{y}@2x.png?mt=bg.ol.ts.ar.lko
```

#### 정정 1 — `@2x` 를 쓴다 (D12). "1차 구현에서는 쓰지 않는다" 를 뒤집는다

위 §3 은 `@2x` 가 유효함을 실측하고도 **"1차 구현에서는 쓰지 않는다"** 로 미뤄뒀다. 그 유보의 근거
두 가지가 모두 틀렸다:

1. **"`tileSize: 512` 와 세트로 가야 한다" 는 틀렸다.** MapLibre 래스터 소스의 `tileSize` 는 타일이
   덮는 **논리적 크기**이며 반환 이미지의 픽셀 크기가 아니다. 512px 이미지를 `tileSize: 256` 으로
   선언하는 것이 바로 "2배 픽셀 밀도로 선명하게 그려라" 라는 뜻이다. `tileSize: 512` 로 바꾸면 줌
   피라미드가 한 단계 밀려 **지도 위치·배율이 어긋난다.** 즉 `@2x` 도입에 `tileSize` 변경은 필요하지
   않고, 오히려 **해서는 안 된다.**
2. **"캐시 타일 수가 1/3 로 줄어든다" 는 사실이지만 유보의 근거가 못 된다.** 코어 기본 지도는 벡터
   타일이라 해상도 독립이었으므로 이 열화가 없었다. 래스터로 내려온 우리에게 Retina 화면의 흐림은
   기능 결손이며, 캐시는 LRU 이므로 워킹셋이 최근 1/3 로 좁아질 뿐이다. 대역폭이 부담인 사용자를
   위해 옵션(`CONF_RETINA`, 기본 켜짐)으로 끌 수 있게 했다.

#### 정정 2 — `.png` 를 쓴다 (D13). `.jpg` 결정을 뒤집는다

`.jpg` 가 1.62배 작다는 것만 보고 내린 결정이었고, **화질 축을 보지 않은 것이 실수였다.** 지도 타일은
글자와 얇은 선이 대부분이며 JPEG 의 블록 잡티가 특히 눈에 띄는 종류의 이미지다. 1.x 가 무손실 `.png`
를 쓴 것이 옳다. 부수 효과로 우리 라우트의 `.png` 확장자와 실제 바이트가 naver 에서는 일치하게 됐지만,
`custom` provider 는 여전히 임의 포맷을 줄 수 있으므로 **`Content-Type` 승계는 계속 필요하다**
(D11 주석을 그 방향으로 정정했다).

#### 신규 발견 — `mt` 레이어 선택자 (D14)

v2.0.1 은 `mt` 파라미터를 아예 붙이지 않았다. 이것이 "버스정류장이 안 뜬다" 의 원인이다.
성분별 실측 (z17, 강남역, `@2x.png`):

| `mt` | 바이트 | 내용 |
|---|---|---|
| `bg` | 155 | 배경만, 사실상 빈 타일 |
| `bg.ol` | 34,041 | + 도로·건물·지하철 노선. **라벨 0** |
| `bg.lko` | 24,201 | + 한글 라벨 |
| `bg.ol.lko` | 47,916 | 라벨 O, 버스정류장 X |
| `bg.ol.ts` | 35,439 | `ts` = 대중교통 |
| `bg.ol.ar` | 34,041 | `ar` 은 이 타일에서 차이 없음 (`bg.ol` 과 md5 동일) |
| **`bg.ol.ts.ar.lko`** | 50,772 | **1.x 값. 전부** |
| 없음 (v2.0.1 동작) | 49,967 | upstream 기본값 — 1.x 값과 **다른 타일이다** |
| `te`, `tr` 등 미지 성분 | — | **HTTP 400** |

- 성분 의미(`bg`=배경, `ol`=도로/외곽선, `ts`=대중교통, `ar`=면, `lko`=한글 라벨)는 **문서화되지 않은
  추정**이다. 확인된 사실은 위 바이트 수와 400 응답뿐이다.
- 미지 성분이 400 을 내므로 이 문자열은 **추측으로 조립하면 안 된다.** `const.py` 의
  `NAVER_MAP_TYPES` 상수 하나만이 근거 있는 출처다.
- `mt` 를 **사용자 옵션으로 만들지 않았다.** 사용자가 "전부(1.x 와 동일)"를 선택했으므로 고정값이다.
- `mt` 값은 upstream 구현 세부사항이므로 **스타일 JSON 을 통해 브라우저로 새어나가지 않아야 한다**
  (AC6). 브라우저에 노출되는 것은 우리 프록시 라우트뿐이다.

#### 최종 확정 URL (실측 검증)

```console
1x .png + mt  → 200 image/png 19,420B  256 x 256
2x .png + mt  → 200 image/png 50,772B  512 x 512
```

```python
# providers.py — naver (v2.1.0)
url_template        = ".../basic/{version}/{z}/{x}/{y}.png?mt=bg.ol.ts.ar.lko"
url_template_retina = ".../basic/{version}/{z}/{x}/{y}@2x.png?mt=bg.ol.ts.ar.lko"
```

타일당 바이트가 16,360(구 `.jpg` 1x) → 50,772(신 `.png` 2x) 로 약 3배다. 32MB 캐시에 담기는 타일 수는
약 650 장으로 줄어든다. **용량 상수는 바꾸지 않았다** — 사용자 옵션이고 eviction 이 LRU 다.

`@2x` 는 naver 만 제공한다. `osm` 은 코어 2026.9 가 "OSM serves no @2x variant" 라고 명시했고
(`02` §3.2), `vworld` 는 1x 템플릿 자체가 미검증이며(§7.2), `custom` 은 임의의 사용자 템플릿이므로 셋
모두 retina 템플릿을 **`None` 으로 둔다. 추측값을 넣지 않았다.**

**클라이언트 → 서버 전달**: `devicePixelRatio` 는 서버가 요청만으로 알 수 없는 유일한 사실이므로
주입 JS 가 스타일 URL 에 `?dpr=<숫자>` 로 전달한다. "스타일 결정은 전부 서버가 한다" 는 기존 원칙의
유일한 예외이며, 여기서도 JS 는 값을 전달만 하고 판단하지 않는다(임계값 `dpr >= 2` 판정은 서버).

---

## 4. 줌 범위와 좌표 범위 밖 동작

### 줌 상한

서울 시청(126.9780, 37.5665) 기준:

```console
z19  447069/203031   200 5322B   md5 340e2a22...
z20  894138/406063   200 2848B   md5 b6430842...
z21  1788276/812126  200 1651B   md5 d6fc3806...
z22  3576552/1624253 200 1651B   md5 d6fc3806...   ← z21 과 동일 바이트
```

강남역(127.0276, 37.4979) 기준 — 밀집 지역:

```console
z18  223570/101578   200 12862B  md5 46afc219...
z19  447141/203157   200  9375B  md5 0b47a5aa...
z20  894282/406315   200  8198B  md5 6c0e6c10...
z21  1788565/812630  200  7230B  md5 cc73edbb...   ← 실제 내용 있음
z22  3577130/1625260 200  5099B  md5 c4ef7da1...   ← 실제 내용 있음
```

**해석 [실측]**
- 서울 시청 z21/z22 의 1651B 동일 바이트는 **그 좌표에 그릴 것이 없는 평면 타일**이다.
  줌 상한이 아니다 — 같은 줌에서 밀집 지역(강남역)은 정상 내용을 반환한다.
- z22 도 200 을 반환하지만 tilejson 이 선언한 상한은 21 이다.
  **선언값을 신뢰한다: `max_native_zoom = 21`.** (코어 `MAP_MAX_ZOOM = 20` 이므로 실사용상 무관하며,
  선언 범위를 넘는 요청을 우리가 upstream 에 흘리지 않는 편이 안전하다.)

### 좌표 범위 밖 — **404 가 아니다**

```console
$ curl ... "https://map.pstatic.net/nrb/styles/basic/1787907321/12/99999/99999.jpg"
200 image/jpeg 1651B   md5 888d888c...
```

z12 의 유효 좌표 상한은 4095 인데, 99999/99999 에도 **200 + 빈 타일**을 반환한다.

> **설계 반영 (중요)**: upstream 이 잘못된 좌표를 걸러주지 않으므로, **좌표 검증은 전적으로 우리
> 프록시의 책임**이다. `03` §3.4 의 검증(자릿수 상한 → `z` 클램프 → `x/y < 2**z`)을 반드시 구현하고,
> 위반 시 upstream 을 호출하지 말고 **즉시 404** 를 반환한다. 구현하지 않으면 임의의 좌표로 무한한
> 캐시 엔트리를 만들 수 있는 캐시 오염·증폭 경로가 된다.

---

## 5. 버전코드가 틀렸을 때

```console
$ curl -s -o /dev/null -w "%{http_code} ct=%{content_type} bytes=%{size_download}\n" \
    "https://map.pstatic.net/nrb/styles/basic/1234567890/12/3492/1586.jpg"
400 ct= bytes=0
```

**[실측]** 무효 버전 → **HTTP 400, 빈 본문**(Content-Type 조차 없음).

> **설계 반영**: 400 은 "버전코드가 만료됐다"는 명확한 신호다.
> - 400 응답은 **캐시하지 않는다**(`03` §3.4 의 "upstream 4xx/5xx → 502, 캐시 안 함" 준수).
> - 400 을 받으면 **버전 갱신을 즉시 트리거**한다(6시간 주기를 기다리지 않는다). 단 재갱신은
>   스로틀링해 400 폭주가 upstream 폭주로 번지지 않게 한다.
> - 갱신 성공 시 새 버전으로 캐시 키가 자동 분리되므로 **명시적 캐시 무효화가 불필요**하다
>   (`03` §3.2 의 "캐시 키에 버전 포함이 더 간단하다 — 권장"이 실측으로 뒷받침된다).

---

## 6. F3 의 파장 — 프록시의 근거를 정정한다

`03-REDESIGN-SPEC.md` §1.2 는 서버측 프록시가 필수인 이유로 두 가지를 든다.
**실측 결과 둘 다 naver provider 에는 해당하지 않는다.**

| §1.2 의 근거 | 실측 |
|---|---|
| ① CORS — `map.pstatic.net` 이 ACAO 를 주지 않아 MapLibre 워커의 `fetch` 가 실패한다 | **틀렸다.** `access-control-allow-origin: *` 가 타일·tilejson 모두에 있다 |
| ② `Referer`/`User-Agent` 로 요청을 식별해야 한다 | **해당 없다.** 네이버는 두 헤더를 요구하지 않는다 |

`03` §0 이 "명세와 사실이 충돌하면 사실이 우선이며 충돌을 발견하면 보고한다"고 규정했으므로 기록한다.
**그러나 설계 결론(프록시 채택)은 바뀌지 않는다.** 근거가 다음으로 교체된다:

1. **API 키 격리 (하드 요구사항)** — `vworld` 와 `custom` provider 는 URL 에 키를 담는다
   (`api.vworld.kr/req/wmts/1.0.0/{api_key}/...`). 스타일 JSON 이나 주입 JS 에 키가 들어가면
   `requires_auth = False` 인 스타일 엔드포인트를 통해 **미인증 사용자에게 키가 노출된다.**
   프록시만이 이를 막는다. naver 하나만 보면 불필요해 보이지만 provider 레지스트리 전체로 보면 필수다.
2. **버전코드를 클라이언트 관심사에서 제거** — 브라우저 직접 호출로 가면 주입 JS 가 버전을 알아야 하고,
   버전이 바뀔 때마다 스타일을 다시 만들어야 한다. 서버가 URL 을 조립하면 프론트엔드는 무상태가 된다
   (주입 JS 30줄이 성립하는 이유). 이것이 `04` §7 이 지적한 `ha-map-card` #94 의 미해결 지점이다.
3. **호출 최소화 = 약관 리스크 완화** — `04` §3 이 요구한 완화책(7일 캐시)은 서버측 캐시가 없으면
   구현할 수 없다. 브라우저 캐시는 우리가 통제하지 못한다.
4. **provider 교체 가능성** — 타일 URL 이 항상 우리 경로이므로 provider 를 바꿔도 프론트엔드는 그대로다.
5. **회전 토큰 재사용** — `/api/map_tiles/` 하위에 두면 코어가 토큰을 붙여준다(`02` §3.3).
   직접 호출 구조로는 이 이점을 못 쓴다.

> ⚠️ 부수 효과: CORS 가 열려 있다는 사실은 **`custom` provider 의 연결 테스트를 서버에서 수행해도
> 브라우저 동작을 보장하지 못한다**는 뜻이기도 하다(반대 방향은 성립). 연결 테스트는 서버측 200 확인까지만
> 책임진다고 문서화한다.

---

## 7. 대조군·대안 provider 실측

### 7.1 OSM (`osm` provider — AC8 대조군)

```console
$ curl -s -o /dev/null -D - -m 15 -A "HomeAssistant/2026.9 (test)" \
    "https://tile.openstreetmap.org/12/3492/1586.png"
HTTP/2 200
content-type: image/png
content-length: 43570
cache-control: max-age=88852, stale-while-revalidate=604800, stale-if-error=604800
access-control-allow-origin: *
access-control-allow-methods: GET, HEAD, OPTIONS
access-control-allow-headers: X-Requested-With
```

**[실측]** 표준 `{z}/{x}/{y}.png`, 256px, ACAO `*`. 코어 `map_tiles` 의 `RASTER_URL` 과 동일 소스이므로
AC8(코어 기본 지도와 시각적 동일) 대조군으로 적합하다.
`stale-if-error=604800` — OSM 스스로도 "장애 시 만료 캐시 사용"을 권한다. `03` §3.3 의
"만료 항목을 즉시 버리지 않는다"와 같은 판단이다.

### 7.2 VWorld (`vworld` provider)

```console
$ curl -s -i -m 15 "https://api.vworld.kr/req/wmts/1.0.0/INVALIDKEY/Base/12/1586/3492.png"
HTTP/1.1 200 200
Access-Control-Allow-Origin: *
Cache-Control: max-age=259200
Content-Length: 448
Set-Cookie: JSESSIONID=...; Path=/; HttpOnly

<?xml version="1.0" encoding="UTF-8"?>
<ExceptionReport xmlns="http://www.opengis.net/ows/1.1" ... version="1.1.0" xml:lang="kor">
  <Exception exceptionCode="InvalidParameterValue" locator="key">
    <ExceptionText><![CDATA[등록되지 않은 인증키입니다.]]></ExceptionText>
  </Exception>
</ExceptionReport>
```

**[실측] 확정 사항 — 구현에 직접 영향**
- **인증 실패에도 HTTP 200 을 반환한다.** 본문만 OWS `ExceptionReport` XML 이다.
  → **`config_flow` 의 연결 테스트를 status code 로 판정하면 잘못된 키를 통과시킨다.**
    `Content-Type` 이 `image/*` 인지 반드시 확인한다.
  → 프록시도 마찬가지다. 200 이지만 `image/*` 가 아닌 응답은 **캐시하지 않고 502** 로 처리한다.
    (naver 만 보고 만든 "4xx/5xx → 502" 규칙으로는 이 케이스가 새어 나간다.)
- `Set-Cookie`(JSESSIONID)를 내려보낸다. **프록시는 upstream 의 `Set-Cookie` 를 클라이언트로
  전달하지 않는다.** 승계할 헤더를 화이트리스트(`Content-Type`, `Content-Encoding`)로 제한한다.
- ACAO `*` 있음, `max-age=259200`(3일).

**[미확인]** — 유효 키가 없어 실측 불가:
- `Base` 외 레이어명(`gray`, `midnight`, `Satellite` 등)의 실제 유효성 (`04` §4.1 의 [미확인] 유지)
- 좌표 순서가 `{z}/{y}/{x}` 라는 `04` §4.1 의 서술 검증 (위 요청은 그 순서로 보냈으나 키 오류가
  먼저 걸려 확인되지 않음)
- 도메인 등록 정책이 서버측(HA) 호출에 어떻게 적용되는가 — Referer 없는 서버 호출이 통과하는지
- 일일 요청 제한 수치

> **설계 반영**: `vworld` provider 는 위 [미확인] 항목 때문에 **1차 구현에서 "무검증 등록"** 상태다.
> 코드에 추측값을 넣지 않고, 좌표 순서는 `04` §4.1 의 서술을 그대로 템플릿에 반영하되
> **주석에 "미검증 — 유효 키로 확인 필요"를 남긴다.** `custom` provider 가 있으므로 사용자가
> 직접 올바른 템플릿을 넣어 우회할 수 있다.

---

## 8. 확정 상수 (코드에 넣을 값)

> ⚠️ 아래 naver 블록의 `url_template` 은 v2.0.1 값이다. **v2.1.0 에서 §3 정정으로 바뀌었다** —
> 현재 유효한 값은 그 아래 블록이다.

```python
# providers.py — naver (v2.0.1, 뒤집힘)
url_template   = "https://map.pstatic.net/nrb/styles/basic/{version}/{z}/{x}/{y}.jpg"
version_meta   = "https://map.pstatic.net/nrb/styles/basic.json"   # TileJSON 2.1.0, key "version"
headers        = {}          # F2: 불필요하나 필드는 유지
attribution    = "© NAVER"   # F8: upstream 이 빈 문자열을 주므로 우리가 명시
tile_size      = 256         # §2
min_zoom       = 0           # tilejson minzoom (코어는 1부터 쓴다)
max_zoom       = 20          # 코어 MAP_MAX_ZOOM 과 정렬
max_native_zoom= 21          # tilejson maxzoom (§4)
url_template_dark = None     # F8: dark 계열 없음 → light 재사용
```

```python
# providers.py — naver (v2.1.0, 현재 유효 — §3 정정 참조)
NAVER_MAP_TYPES = "bg.ol.ts.ar.lko"        # const.py. 성분별 실측표는 §3 정정에
url_template        = ".../basic/{version}/{z}/{x}/{y}.png?mt=bg.ol.ts.ar.lko"     # D13+D14
url_template_retina = ".../basic/{version}/{z}/{x}/{y}@2x.png?mt=bg.ol.ts.ar.lko"  # D12+D13+D14
url_template_dark_retina = None            # F8: dark 계열이 없으므로 dark @2x 도 없다
tile_size      = 256         # ★ scale 2 에서도 256. 512 로 바꾸면 줌 피라미드가 밀린다
# 나머지 필드는 위 블록과 동일하다.
```

```python
# providers.py — osm (대조군)
url_template = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
headers      = {"User-Agent": f"HomeAssistant/{__version__} (naver_map_change)"}  # OSMF 정책
attribution  = "© OpenStreetMap contributors"
tile_size, min_zoom, max_zoom, max_native_zoom = 256, 1, 20, 19
```

```python
# providers.py — vworld  (⚠ 미검증: 유효 키 없이 실측 불가. §7.2)
url_template = "https://api.vworld.kr/req/wmts/1.0.0/{api_key}/Base/{z}/{y}/{x}.png"  # y/x 순서
needs_api_key = True
attribution  = "© 국토교통부 공간정보 오픈플랫폼(VWorld)"
```

### 실측이 바꾼 구현 요건 체크리스트

- [ ] 좌표 검증을 프록시에서 강제한다 (§4 — upstream 이 200 빈 타일을 주므로 필수)
- [ ] upstream 200 이지만 `Content-Type` 이 `image/*` 가 아니면 502, 캐시하지 않는다 (§7.2)
- [ ] 승계 헤더 화이트리스트: `Content-Type`, `Content-Encoding` 만. `Set-Cookie` 차단 (§7.2)
- [ ] upstream 400 → 버전 갱신 즉시 트리거(스로틀링 포함), 캐시하지 않는다 (§5)
- [ ] 캐시 키에 `version` 포함 → 버전 변경이 자동 무효화 (§5, AC12)
- [ ] 스타일 JSON 의 `attribution` 은 provider 상수에서 나온다. upstream 값(`""`)을 쓰지 않는다 (§1)
- [ ] ~~`.png` 라우트가 `image/jpeg` 를 서빙하는 이유를 코드 주석에 남긴다 (§3)~~
      → **정정(D13)**: naver 는 `.png` 를 요청하므로 불일치가 사라졌다. 다만 `custom` provider 는 임의
      포맷을 줄 수 있으므로 `Content-Type` 승계가 여전히 필요하다는 쪽으로 주석을 정정한다 (§3 정정)
- [ ] `mt=bg.ol.ts.ar.lko` 를 붙인다. 값은 `const.py` 상수 하나에서만 온다 (§3 정정, D14)
- [ ] `@2x` 를 dpr 인식으로 붙인다. **`tileSize` 는 256 을 유지한다** (§3 정정, D12)
- [ ] 캐시 키에 `scale` 포함 → 1x/2x 가 섞이지 않는다 (§3 정정, D12)

---

## 9. 재현 방법

이 문서의 모든 수치는 아래로 재현된다. `version` 은 바뀌므로 매번 tilejson 에서 읽는다.

```bash
V=$(curl -s https://map.pstatic.net/nrb/styles/basic.json \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])')
echo "version=$V"

# 서울 시청 z12 타일 — 헤더 없이
curl -s -o /tmp/t.jpg -D - "https://map.pstatic.net/nrb/styles/basic/$V/12/3492/1586.jpg"
file /tmp/t.jpg

# 좌표 범위 밖 (200 + 빈 타일을 확인)
curl -s -o /dev/null -w "%{http_code} %{size_download}\n" \
  "https://map.pstatic.net/nrb/styles/basic/$V/12/99999/99999.jpg"

# 무효 버전 (400 확인)
curl -s -o /dev/null -w "%{http_code} %{size_download}\n" \
  "https://map.pstatic.net/nrb/styles/basic/1234567890/12/3492/1586.jpg"
```

---

## 10. 부록 — 코어 2026.9.0 원본 직접 검증 (설계 열쇠 확인)

`02-HA-PLATFORM-2026.md` 는 `home-assistant/frontend` **dev 브랜치 소스**를 근거로 삼았다.
여기서는 **실제 배포 산출물**로 재확인했다. 검증 환경:

```bash
python3 -m venv hav
./hav/bin/pip install homeassistant pytest-homeassistant-custom-component
./hav/bin/pip install home-assistant-frontend==20260826.4
./hav/bin/python -c "from homeassistant.const import __version__; print(__version__)"
# → 2026.9.0
```

### 10.1 토큰 저장소 키 — `hass.data["map_tiles"]` 확정 [직접검증]

```console
$ grep -nE "DATA_ACCESS_TOKENS" site-packages/homeassistant/components/map_tiles/const.py
14:DATA_ACCESS_TOKENS: HassKey[deque[str]] = HassKey(DOMAIN)
```

`DOMAIN = "map_tiles"` 이므로 `hass.data["map_tiles"]` 의 값은 **`deque[str]` (maxlen=2)** 이다.
`03` §3.4 의 `hass.data.get("map_tiles")` 접근이 정확하다.

`_MapTilesView._authenticate` 원본도 `02` §3.4 의 인용과 **한 글자도 다르지 않다.** 403 을 쓰는 이유가
코어 주석에 명시돼 있다: *"Most likely a query token that expired while a dashboard sat open, so 403
rather than banning the user's own IP over it."*

### 10.2 좌표 검증 — 코어 참조 구현 [직접검증]

```python
# map_tiles/views.py:50
# Cap coordinate length before int(), which is expensive on huge digit strings.
MAX_COORDINATE_DIGITS = 8

# map_tiles/views.py:171~182  (_MapTilesTileView.get)
if any(len(part) > MAX_COORDINATE_DIGITS for part in (z, x, y)):
    return web.Response(status=HTTPStatus.NOT_FOUND)
zoom, column, row = int(z), int(x), int(y)
if zoom > self.max_zoom or column >= 2**zoom or row >= 2**zoom:
    return web.Response(status=HTTPStatus.NOT_FOUND)
```

라우트 자체에도 정규식 제약이 걸려 있다 — 비숫자·음수가 라우팅 레이어에서 걸러진다:

```python
url = "/api/map_tiles/raster/{z:[0-9]+}/{x:[0-9]+}/{y:[0-9]+}.png"
```

> **설계 반영**: 우리 뷰 URL 도 같은 정규식 제약을 쓴다. §4 의 파이썬측 검증은 이중 방어로 유지한다.

### 10.3 `/static/map/light.json` 은 실제 존재하는 정적 파일 [직접검증]

```console
$ ls -l site-packages/hass_frontend/static/map/
131551  light.json     6666  light.json.br     7907  light.json.gz
130998  dark.json      6635  dark.json.br      7880  dark.json.gz
133355  mapbox-gl-rtl-text.js  ...

$ head -c 300 site-packages/hass_frontend/static/map/light.json
{"version":8,"name":"versatiles-colorful","metadata":{...},
 "glyphs":"/api/map_tiles/fonts/{fontstack}/{range}.pbf",
 "sprite":[{"id":"basics","url":"/api/map_tiles/sprites/basics/sprites"}],
 "sources":{"versatiles-shortbread":{"type":"vector","url":"/api/map_tiles/tilejson.json"}},
 "layers":[{"id":"background","type":"background",...
```

- 스타일 스펙 **v8** 확정. 우리 래스터 스타일도 `"version": 8` 이면 구조적으로 호환된다.
- 코어 벡터 스타일은 131KB / 레이어 수백 개다. 우리 래스터 스타일은 소스 1개 + 레이어 1개로 끝난다.
- 베이스맵은 `versatiles-colorful` (VersaTiles/Shortbread, CC0) 이다.

### 10.4 ★ `withMapTilesToken` — 하위 경로 화이트리스트가 없다 [직접검증]

배포 번들 `frontend_latest/52451.1a5550dd73d8854a.js` 의 모듈 `64353` 을 추출했다.
`rG` 가 `withMapTilesToken` 이고, `o = "/api/map_tiles"` 다:

```js
const o = "/api/map_tiles";
// ...
rG: t => {
  let e;
  try { e = new URL(t, b()) } catch { return t }
  if (!e.pathname.startsWith(`${o}/`)) return e.href;   // ← 접두사 검사 하나뿐
  const i = new URL(`${b()}${e.pathname}${e.search}`);
  return n && i.searchParams.set("token", n), i.href;   // n = 현재 토큰
}
```

같은 모듈에서 확인된 것:

| 최소화 심볼 | 원래 이름 | 확인 내용 |
|---|---|---|
| `o` | `MAP_TILES_PATH` | `"/api/map_tiles"` |
| `rG` | `withMapTilesToken` | 접두사 검사 후 `?token=` 부착. **하위 경로 화이트리스트 없음** |
| `bK` | `mapTilesUrl` | `t.startsWith("/") ? `${origin}${t}` : t` — 루트 상대 경로를 절대화 |
| `12e5` | `TOKEN_REFRESH_MS` | 1,200,000ms = **20분** 주기 `setInterval` 재발급 |
| `g` | — | WS 명령 `{type:"map_tiles/access_token"}` 호출 |

그리고 스타일 URL 상수도 배포 번들에 그대로 있다(모듈 `64758`):

```js
const o = { light: "/static/map/light.json", dark: "/static/map/dark.json" },
      r = `${a.ZV}/raster/{z}/{x}/{y}.png?token={token}`;
```

> **결론 [직접검증]**: `03-REDESIGN-SPEC.md` §1.3 이 "설계의 핵심"으로 삼은 가정 —
> *"`/api/map_tiles/` 로 시작하는 모든 URL 에 유효 토큰이 붙는다"* — 이 **dev 브랜치 소스뿐 아니라
> 2026.9.0 배포 번들에서도 사실이다.** 따라서 `/api/map_tiles/naver_map_change/{z}/{x}/{y}.png` 는
> 코어의 `transformRequest` 로부터 유효한 회전 토큰을 자동으로 받는다. 자체 토큰 발급 체계가 불필요하다.
> 주입 JS 가 가로챌 대상 문자열(`/static/map/light.json`) 역시 배포 번들에 그대로 존재한다.

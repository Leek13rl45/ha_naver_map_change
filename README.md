# Naver Map Change for Home Assistant

HA 기본 지도(OpenStreetMap)를 **네이버 지도**로 교체하는 커스텀 통합구성요소입니다.  
버전코드를 **자동으로 최신 상태**로 갱신합니다.

---

## 특징

- ✅ 네이버 지도 타일 버전코드 **자동 갱신**
- ✅ 원본 JS 파일 **자동 백업**
- ✅ 원본 복원 서비스 제공
- ✅ HA 설치 경로 **자동 탐지** (HAOS, venv, Docker)

---

## 설치 방법

### 수동 설치
1. `custom_components/naver_map_change` 폴더를 HA config 디렉토리에 복사합니다.
2. Home Assistant를 재시작합니다.

### HACS (Custom Repository)
1. HACS → Integrations → 우측 상단 메뉴 → Custom repositories
2. URL 입력 후 category: `Integration` 선택

---

## 사용 방법

### 1. 통합구성요소 추가
**구성 → 통합구성요소 → 통합구성요소 추가** → `Naver Map Change` 선택

### 2. 네이버 지도 적용
**개발자 도구 → 서비스 → `naver_map_change.apply`** 실행

### 3. 브라우저 캐시 초기화
`Ctrl + Shift + R` (강력 새로고침)

---

## 서비스

| 서비스 | 설명 |
|--------|------|
| `naver_map_change.apply` | 네이버 지도 적용 (버전코드 자동 갱신 포함) |
| `naver_map_change.restore` | 원본 OSM 지도로 복원 |

---

## 주의사항

- HA 업데이트 후에는 JS 파일이 초기화되므로 서비스를 다시 실행해야 합니다.
- 파일 쓰기 권한이 필요합니다.
- Docker/HAOS 환경에서는 컨테이너 내부 파일이 수정되므로 재시작 시 초기화될 수 있습니다.

---

## 자동화 예시 (HA 시작 시 자동 적용)

```yaml
automation:
  - alias: "시작 시 네이버 지도 자동 적용"
    trigger:
      - platform: homeassistant
        event: start
    action:
      - delay: "00:00:30"
      - service: naver_map_change.apply
```

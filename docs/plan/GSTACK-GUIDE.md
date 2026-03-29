# gstack 사용 가이드 — autoReply 프로젝트

> Cursor 채팅창에서 `/` 를 입력하면 gstack 명령 목록이 보인다.  
> 명령 이름은 `gstack-` 접두어가 붙는다 (예: `/gstack-review`, `/gstack-qa`)

---

## 명령 목록

| 명령 | 역할 | 추천 모델 |
|------|------|-----------|
| `/gstack-office-hours` | 아이디어 정리, 핵심 문제 정의, 디자인 문서 생성 | Max (Opus급) |
| `/gstack-plan-ceo-review` | 제품 관점 검토: 범위 축소, MVP 정의, 우선순위 | Max (Opus급) |
| `/gstack-plan-eng-review` | 기술 관점 검토: 아키텍처, 데이터 모델, API, 위험 요소 | Max (Opus급) |
| `/gstack-review` | 코드 리뷰: 버그, 보안, 회귀, 테스트 공백 | 기본 (Sonnet급) |
| `/gstack-qa` | 기능/보안/에러/DB/UI 테스트 체크리스트 | 기본 (Sonnet급) |
| `/gstack-ship` | 테스트 확인, PR 생성, 릴리즈 노트 작성 | 기본 (Sonnet급) |

### 모델 선택 기준

- **"생각"이 중요한 명령** (office-hours, plan-ceo, plan-eng) → Max 모델
- **"실행"이 중요한 명령** (review, qa, ship) → 기본 모델
- 비용 절약 시: plan 단계만 Max 모델, 나머지는 기본 모델로 운영

---

## 상황별 사용 흐름

### 1. 최초 개발 (프로젝트를 처음 만들 때)

처음부터 끝까지 전체 흐름을 다 돌린다.

```
/gstack-office-hours       [Max] 아이디어 정리, 핵심 문제 정의, 디자인 문서 생성
        |
/gstack-plan-ceo-review    [Max] 제품 관점 검토: 범위 축소, MVP 정의, 우선순위
        |
/gstack-plan-eng-review    [Max] 기술 관점 검토: 아키텍처, 데이터 모델, API, 위험 요소
        |
        v
      [구현]                plan에서 확정된 범위대로 코드 작성
        |
/gstack-review             [기본] 코드 리뷰: 버그, 보안, 회귀, 테스트 공백
        |
/gstack-qa                 [기본] 기능/보안/에러/DB/UI 테스트 체크리스트
        |
/gstack-ship               [기본] 테스트 확인, PR 생성, 릴리즈 노트
```

- `/gstack-office-hours`부터 시작해서 "뭘 만들 것인가"를 먼저 정리
- CEO review → Eng review로 범위와 기술 구조를 확정한 뒤 구현에 들어간다

---

### 2. 기능 추가 (이미 돌아가는 프로젝트에 새 기능)

제품 방향이 이미 있으므로 `/gstack-office-hours`는 건너뛴다.

```
/gstack-plan-ceo-review    [Max] 새 기능의 제품 가치 + 기존 기능 영향 검토
        |
/gstack-plan-eng-review    [Max] 기존 코드 영향 분석 (DB 마이그레이션, API 변경, UI 변경)
        |
        v
      [구현]                변경 범위 최소화하며 구현
        |
/gstack-review             [기본] 기존 기능 회귀 + 새 기능 버그 중심
        |
/gstack-qa                 [기본] 새 기능 + 기존 기능 회귀 테스트
        |
/gstack-ship               [기본] 릴리즈 노트에 새 기능 + 마이그레이션 주의사항
```

- `/gstack-plan-eng-review`에서 FastAPI 라우터, Graph API, poller, WebSocket, Alembic 영향을 구조적으로 검토하는 게 핵심
- `/gstack-review`에서 기존 기능 회귀 체크를 더 강하게

---

### 3. 버그 수정

plan 단계 없이 `/gstack-review`부터 시작한다.

```
/gstack-review             [기본] 현재 코드 문제점 분석
        |
      [수정]               버그 수정
        |
/gstack-qa                 [기본] 수정 확인 + 관련 기능 회귀 테스트
        |
/gstack-ship               [기본] 패치 릴리즈 (PATCH 버전)
```

- 범위가 좁으므로 빠르게 순환
- 수정 전에 반드시 `/gstack-review`로 원인 파악 먼저

---

### 4. 배포 전 최종 점검

```
/gstack-review             [기본] 전체 diff 검토
        |
/gstack-qa                 [기본] 전체 기능 점검
        |
/gstack-ship               [기본] 릴리즈
```

---

### 5. 아이디어 브레인스토밍 (코드 없이)

```
/gstack-office-hours       [Max] 아이디어 탐색, 가정 검증, 디자인 문서 생성
```

- 코드를 쓰기 전에 아이디어만 정리하고 싶을 때
- 결과는 `docs/plan/`에 자동 저장됨

---

## autoReply 프로젝트 연결 규칙

각 gstack 명령 실행 시 이 프로젝트의 기존 규칙과 연결된다:

| gstack 명령 | 연결되는 규칙 | 산출물 |
|-------------|--------------|--------|
| `/gstack-office-hours`, `/gstack-plan-*` | — | `docs/plan/PLAN.md` 또는 세부 plan 파일 |
| `/gstack-review` | `owasp-security.mdc` 보안 체크리스트 포함 | 리뷰 리포트 |
| `/gstack-qa` | `plan-documentation.mdc` 테스트 체크리스트 형식 | `docs/test/phase-*.md` |
| `/gstack-ship` | `github-upload.mdc` 릴리즈 절차 참고 | `docs/release/vX.Y.Z.md` + git push |

---

## 향후 확장 가능한 명령

핵심 6개에 익숙해지면 필요에 따라 바로 사용 가능:

| 명령 | 용도 |
|------|------|
| `/gstack-cso` | OWASP + STRIDE 보안 감사 |
| `/gstack-investigate` | 조사 없이 수정하지 않는 디버깅 흐름 |
| `/gstack-retro` | 주간 회고 + 배포 기록 분석 |
| `/gstack-browse` | 실제 브라우저로 UI 테스트 |
| `/gstack-careful` | 파괴적 명령(`rm -rf`, `DROP TABLE`) 실행 전 경고 |
| `/gstack-freeze` | 파일 편집을 특정 디렉터리로 제한 |
| `/gstack-upgrade` | gstack 최신 버전으로 업그레이드 |

---

## 설치 정보

- 스킬 위치: `.agents/skills/gstack-*/SKILL.md` (프로젝트 로컬)
- gstack 소스: `.cursor/skills/gstack/` (수정 가능)
- 설치 기준: [Cursor Agent Skills 공식 문서](https://cursor.com/docs/skills)
- gstack 원본: [github.com/garrytan/gstack](https://github.com/garrytan/gstack)

### 업그레이드 방법

```
/gstack-upgrade
```

또는 수동으로:

```powershell
cd .cursor/skills/gstack
git pull
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/project/autoReply/.cursor/skills/gstack && ./setup --host codex --no-prefix"
```

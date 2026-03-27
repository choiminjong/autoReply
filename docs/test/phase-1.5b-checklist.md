# Phase 1.5b 테스트 체크리스트

> Build Date: 2026-03-25
> Scope: Outlook 커넥터 분리, Graph API 스코프 확장, 멀티유저 폴러, WebSocket 멀티유저, 설정 페이지 UI

---

## 1. Outlook 커넥터

- [ ] `/api/outlook/connect` 호출 시 Microsoft OAuth 페이지로 리다이렉트되는가?
- [ ] OAuth 인증 완료 후 `/api/outlook/callback`이 올바르게 토큰을 교환하는가?
- [ ] 교환된 토큰이 `outlook_tokens` 테이블에 AES-256-GCM 암호화되어 저장되는가?
  - DB 파일을 직접 열어 `access_token` 컬럼이 평문이 아닌지 확인
- [ ] `/api/outlook/status` 가 `{"connected": true, "ms_email": "..."}` 를 반환하는가?
- [ ] `/api/outlook/disconnect` (DELETE) 실행 후 토큰이 삭제되고 `connected: false`가 반환되는가?
- [ ] 미인증 상태에서 위 엔드포인트 호출 시 401이 반환되는가?

---

## 2. Graph API 스코프

- [ ] `app/config.py`의 `scope` 프로퍼티에 `Mail.Send`, `Mail.ReadWrite`, `People.Read`가 포함되어 있는가?
- [ ] 재인증(Outlook 재연동) 후 새 스코프가 반영되는가?
  - Microsoft 동의 화면에 해당 권한들이 나타나는지 확인

---

## 3. 멀티유저 폴러

- [ ] 2명 이상 Outlook 연동 후 서버 시작 시 두 유저 모두 delta sync가 실행되는가?
- [ ] 토큰 만료 시 자동 refresh가 이루어지고 갱신된 토큰이 암호화되어 저장되는가?
- [ ] Graph API 429 응답 시 지수 백오프로 재시도하는가? (로그 확인)
- [ ] 신규 메일 수신 시 해당 유저의 WebSocket으로 이벤트가 전달되는가?

---

## 4. WebSocket 멀티유저

- [ ] `/ws` 엔드포인트 접속 시 세션 쿠키 인증이 수행되는가?
  - 쿠키 없이 접속 시 연결이 즉시 종료되는가?
- [ ] `{"type": "set_project", "project_id": "..."}` 메시지 전송 후 프로젝트 채널로 이동되는가?
- [ ] 두 유저가 동시 접속했을 때 user-specific 이벤트가 각자에게만 전달되는가?
- [ ] 유저가 특정 프로젝트를 설정한 뒤 project-specific 이벤트를 수신하는가?

---

## 5. 설정 페이지 UI

- [ ] `/settings` 접속 시 로그인 여부를 확인하고, 미인증 시 `/login` 으로 리다이렉트되는가?
- [ ] 프로필 섹션에 로그인 유저의 이름, 이메일, 역할이 표시되는가?
- [ ] Outlook 미연동 상태에서 "Outlook 연동" 버튼이 표시되는가?
- [ ] Outlook 연동 상태에서 MS 계정 이메일과 연동일, "연동 해제" 버튼이 표시되는가?
- [ ] "연동 해제" 클릭 → 확인 다이얼로그 → 해제 후 UI가 미연동 상태로 갱신되는가?
- [ ] `/api/folders` 응답을 기반으로 폴더 목록이 렌더링되는가?
- [ ] 폴더 체크박스 토글 후 "설정 저장" 클릭 시 `POST /api/folders/save-selection` 요청이 전송되는가?
- [ ] "🌐 팀공유 / 🔒 비공개" 버튼 클릭 시 `PATCH /api/folders/{id}/visibility` 요청이 전송되고 UI가 즉시 갱신되는가?
- [ ] "↻ 새로고침" 클릭 시 `POST /api/folders/refresh` 요청이 전송되고 폴더 목록이 갱신되는가?
- [ ] 모든 PATCH/POST/DELETE 요청에 `X-CSRF-Token` 헤더가 첨부되는가?

---

## 공통

- [ ] 설정 페이지에서 에러 발생 시 토스트 메시지가 표시되는가?
- [ ] 헤더의 "← 칸반 보드" 링크가 `/`로 정상 이동되는가?
- [ ] `main.py`에 `/settings` 라우트가 등록되어 있는가?

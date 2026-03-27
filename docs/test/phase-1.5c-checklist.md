# Phase 1.5c 테스트 체크리스트

> Build Date: 2026-03-25
> Scope: 프로젝트 시스템, 쓰레드-프로젝트 매핑, 폴더 권한, 클레임 시스템, 팀 댓글, UI

---

## 1. DB 마이그레이션

- [ ] 서버 시작 시 `002_add_projects` 마이그레이션이 정상 실행되는가?
- [ ] `projects`, `project_members`, `comments`, `mentions`, `user_settings` 테이블이 생성되었는가?
- [ ] `threads` 테이블에 `user_id`, `project_id`, `claimed_by` 컬럼이 추가되었는가?
- [ ] 기존 데이터가 손상되지 않았는가?

---

## 2. 프로젝트 CRUD

- [ ] `POST /api/projects` — 프로젝트 생성 시 생성자가 owner로 자동 등록되는가?
- [ ] `GET /api/projects` — 내 프로젝트만 조회되는가? (admin은 전체)
- [ ] `GET /api/projects/{id}` — 비멤버가 접근 시 403이 반환되는가?
- [ ] `PATCH /api/projects/{id}` — owner/admin만 수정 가능한가?
- [ ] `DELETE /api/projects/{id}` — 삭제 시 threads.project_id가 NULL로 변경되는가?

### 멤버 관리

- [ ] `POST /api/projects/{id}/members` — owner/admin만 추가 가능한가?
- [ ] `DELETE /api/projects/{id}/members/{uid}` — 마지막 owner 제거 시 400이 반환되는가?
- [ ] 멤버 제거 시 해당 사용자의 클레임이 해제되는가?
- [ ] `PATCH /api/projects/{id}/members/{uid}` — role 변경이 정상 동작하는가?

---

## 3. 쓰레드-프로젝트 매핑

- [ ] 메일 수신 시 `to_recipients`에 프로젝트 `mailing_list`가 포함되면 자동 매핑되는가?
- [ ] `PATCH /api/threads/{cid}/project` — 수동으로 프로젝트 지정/해제가 되는가?
- [ ] `GET /api/threads?project_id=...` — 특정 프로젝트의 쓰레드만 필터링되는가?
- [ ] `GET /api/threads?view=unclaimed` — 미클레임 쓰레드만 반환되는가?
- [ ] `GET /api/threads?view=mine` — 내가 클레임한 쓰레드만 반환되는가?

---

## 4. 클레임 시스템

- [ ] `POST /api/threads/{cid}/claim` — 미클레임 쓰레드에 클레임이 성공하는가?
- [ ] 이미 클레임된 쓰레드에 다른 사용자가 클레임 시 409가 반환되는가?
- [ ] 두 사용자가 동시에 클레임할 때 낙관적 잠금으로 하나만 성공하는가?
- [ ] 메일 회신 시 `claimed_by`가 자동으로 나로 설정되는가? (기존 클레임 없는 경우)
- [ ] `DELETE /api/threads/{cid}/claim` — 자신의 클레임만 해제 가능한가?
- [ ] admin은 다른 사람의 클레임도 해제 가능한가?
- [ ] 클레임 변경 시 WebSocket `claim_changed` 이벤트가 전송되는가?

---

## 5. 팀 댓글

- [ ] `POST /api/threads/{cid}/comments` — 댓글이 저장되는가?
- [ ] `@[이름](user_id)` 형태의 멘션이 파싱되어 `mentions` 테이블에 저장되는가?
- [ ] 멘션된 사용자에게 WebSocket `mention` 이벤트가 전송되는가?
- [ ] `GET /api/threads/{cid}/comments` — 페이지네이션이 동작하는가?
- [ ] `DELETE /api/threads/{cid}/comments/{id}` — 자신의 댓글만 삭제 가능한가?
- [ ] `GET /api/mentions/unread` — 읽지 않은 멘션 목록이 반환되는가?
- [ ] `PATCH /api/mentions/{id}/read` — 읽음 처리가 되는가?

---

## 6. 프로젝트 선택 UI (`/projects`)

- [ ] 로그인 후 `/projects` 접속 시 내 프로젝트 목록이 표시되는가?
- [ ] 각 프로젝트 카드에 팀원 수, 미클레임 건수가 표시되는가?
- [ ] 역할(owner/member) 배지가 표시되는가?
- [ ] `+ 새 프로젝트` 클릭 시 생성 모달이 열리는가?
- [ ] 프로젝트 카드 클릭 시 `sessionStorage`에 project_id가 저장되고 `/`로 이동하는가?

---

## 7. 칸반보드 팀 뷰

- [ ] `sessionStorage`에 프로젝트가 설정된 경우 해당 프로젝트 쓰레드만 표시되는가?
- [ ] 각 카드에 클레임 배지(처리 중/미클레임)가 표시되는가?
- [ ] 카드 클릭 시 상세 패널의 클레임 바에 현재 상태가 표시되는가?
- [ ] "클레임" 버튼 클릭 → 클레임 성공 → 버튼이 "해제"로 바뀌는가?
- [ ] 이미 클레임된 경우 클레임 버튼 없이 담당자 이름만 표시되는가?

---

## 8. 댓글 탭 UI

- [ ] 상세 패널에 "📧 메일 쓰레드 | 💬 팀 댓글" 탭이 표시되는가?
- [ ] "팀 댓글" 탭 클릭 시 댓글 목록이 로드되는가?
- [ ] 댓글 입력 후 "댓글 달기" 클릭 시 바로 목록에 추가되는가?
- [ ] 멘션 태그(@이름)가 파란색으로 표시되는가?
- [ ] 새 댓글 WebSocket 이벤트 수신 시 댓글 탭이 자동 새로고침되는가?
- [ ] "메일 쓰레드" 탭으로 돌아오면 기존 회신 영역이 정상 표시되는가?

---

## 공통

- [ ] 모든 POST/PATCH/DELETE 요청에 CSRF 토큰이 첨부되는가?
- [ ] 비인증 사용자가 API 접근 시 401이 반환되는가?
- [ ] 에러 발생 시 적절한 토스트 메시지가 표시되는가?

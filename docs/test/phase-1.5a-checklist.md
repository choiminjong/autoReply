# Phase 1.5a 테스트 체크리스트

## 1. 기능 테스트

### 회원가입
- [ ] `/register` 페이지 정상 표시
- [ ] 최초 회원가입 시 role=admin 자동 지정
- [ ] 두 번째 회원가입 시 role=user 지정
- [ ] 이메일 중복 시 409 에러 반환
- [ ] 비밀번호 7자 이하 시 400 에러 반환
- [ ] 회원가입 후 자동 로그인 및 `/` 리다이렉트

### 로그인
- [ ] `/login` 페이지 정상 표시
- [ ] 올바른 이메일/비밀번호로 로그인 성공
- [ ] 잘못된 비밀번호로 401 에러 반환
- [ ] 로그인 후 `session_id` 쿠키 발급 확인
- [ ] 로그인 후 `csrf_token`이 응답에 포함되어 localStorage에 저장
- [ ] 로그인 후 `/` 리다이렉트

### 세션 / 인증
- [ ] 로그인 상태에서 `/api/auth/me` 200 응답
- [ ] 미로그인 상태에서 `/api/threads` 401 응답
- [ ] 미로그인 상태에서 `/` 접근 시 `/login` 리다이렉트
- [ ] 로그아웃 후 세션 쿠키 삭제 확인
- [ ] 로그아웃 후 `/` 접근 시 `/login` 리다이렉트

### CSRF 보호
- [ ] `X-CSRF-Token` 헤더 없이 POST 요청 시 403 반환
- [ ] 올바른 CSRF 토큰으로 POST 요청 성공

### 암복호화
- [ ] `python -c "from app.services.crypto import encrypt, decrypt, get_key; k=get_key(); assert decrypt(encrypt('test', k), k) == 'test'; print('OK')"` 성공

## 2. 보안 테스트

- [ ] 비밀번호가 bcrypt 해시로 저장되어 평문 아님 확인 (DB에서 `$2b$` 접두사)
- [ ] `ENCRYPTION_KEY` 없이 시작 시 `ValueError` 발생 확인
- [ ] admin 아닌 사용자가 `/api/admin/users` 접근 시 403 반환
- [ ] 미로그인 상태에서 `/admin` 접근 시 `/` 리다이렉트

## 3. DB 테스트

- [ ] `users`, `sessions`, `outlook_tokens`, `app_settings` 테이블 생성 확인
- [ ] Alembic `alembic_version` 테이블에 revision `001` 기록 확인
- [ ] `app_settings`에 기본값 4개 삽입 확인 (`default_session_hours=8` 등)
- [ ] 기존 테이블 (`threads`, `messages` 등) 데이터 유지 확인

## 4. UI 테스트

- [ ] 로그인 페이지 다크 테마 정상 표시
- [ ] 회원가입 페이지 정상 표시
- [ ] 로그인 후 칸반 보드 헤더에 사용자 이름/이메일 표시
- [ ] 헤더 네비게이션 (설정, 관리, 로그아웃 버튼) 표시
- [ ] admin 계정으로 로그인 시 "관리" 버튼 표시
- [ ] 일반 user 계정으로 로그인 시 "관리" 버튼 숨김
- [ ] Outlook 미연동 상태에서 상단 배너 표시

## 5. 에러 핸들링 테스트

- [ ] 존재하지 않는 API 엔드포인트에 500이 아닌 404 반환
- [ ] 서버 로그에 구조화된 로그 형식 확인 (`YYYY-MM-DD HH:MM:SS [INFO] ...`)
- [ ] 로그인 실패 시 상세한 에러 메시지 (이메일/비밀번호 오류 구분 없이 보안상 동일 메시지)

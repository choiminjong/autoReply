#!/bin/bash
set -e

echo "=== Mail Kanban — Team AutoReply 설정 ==="

# Python 버전 확인
python_version=$(python --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "Python 버전: $python_version"

# 가상환경 생성
if [ ! -d "venv" ]; then
    echo "가상환경 생성 중..."
    python -m venv venv
    echo "가상환경 생성 완료"
else
    echo "가상환경이 이미 존재합니다"
fi

# 가상환경 활성화 (Windows Git Bash)
if [ -f "venv/Scripts/activate" ]; then
    source venv/Scripts/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

echo "의존성 설치 중..."
pip install -r requirements.txt --quiet
echo "의존성 설치 완료"

# data 폴더 생성
mkdir -p data docs/build docs/test docs/plan docs/release

# .env 파일 생성 및 ENCRYPTION_KEY 자동 생성
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo ".env 파일이 생성되었습니다."
fi

# ENCRYPTION_KEY 자동 생성 (없는 경우)
if grep -q "^ENCRYPTION_KEY=$" .env; then
    echo "ENCRYPTION_KEY 생성 중..."
    ENCRYPTION_KEY=$(python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())")
    
    # Windows/Mac 모두 호환
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i "" "s|^ENCRYPTION_KEY=$|ENCRYPTION_KEY=${ENCRYPTION_KEY}|" .env
    else
        sed -i "s|^ENCRYPTION_KEY=$|ENCRYPTION_KEY=${ENCRYPTION_KEY}|" .env
    fi
    echo "ENCRYPTION_KEY 생성 완료 (32 bytes, base64)"
    echo ""
    echo "⚠️  중요: .env 파일을 안전한 곳에 백업하세요!"
    echo "   DB를 초기화하면 ENCRYPTION_KEY가 없으면 암호화된 토큰을 복호화할 수 없습니다."
else
    echo "ENCRYPTION_KEY가 이미 설정되어 있습니다."
fi

echo ""
echo "=== 설정 완료 ==="
echo ""
echo "서비스 실행 방법:"
echo "  source venv/Scripts/activate  (Windows)"
echo "  source venv/bin/activate      (Mac/Linux)"
echo "  python main.py"
echo ""
echo "브라우저에서 http://localhost:8000 접속"
echo ""
echo "최초 접속 시:"
echo "  1. /register 에서 계정 생성 (첫 번째 계정이 자동으로 admin)"
echo "  2. /login 에서 로그인"
echo "  3. 설정 > Outlook 연동 클릭"
echo ""
echo "Azure Portal 필수 설정:"
echo "  Redirect URI: http://localhost:8000/api/outlook/callback"

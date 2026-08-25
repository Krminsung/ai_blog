# BlogOps AI — Frontend

BlogOps AI 백엔드(`/root/ai_blog`, FastAPI)를 위한 웹 프론트엔드입니다. 마케팅
사이트와 운영 콘솔을 함께 제공하며, 백엔드 코드는 전혀 수정하지 않고 공개된
`/v1` HTTP 경계만 사용합니다.

## 스택

| 항목 | 선택 |
| --- | --- |
| 프레임워크 | Next.js 16 (App Router, Turbopack) |
| 언어 | TypeScript (strict) |
| 스타일 | Tailwind CSS v4, CSS 변수 기반 테마 토큰 |
| 데이터 | SWR + 자체 fetch 클라이언트 |
| 타입 | 백엔드 OpenAPI에서 생성 (`openapi-typescript`) |

외부 UI 라이브러리를 쓰지 않습니다. 버튼, 모달, 토스트, 테이블 등은
`src/components/ui`에 직접 구현되어 있습니다.

## 디자인 언어

apple.com의 표현 방식을 기준으로 삼았습니다.

- **타이포그래피**: 시스템 서체(SF Pro / Apple SD Gothic Neo / Pretendard)를
  사용하고, 디스플레이 크기에서 자간을 −0.035em까지 좁힙니다.
- **여백**: 980–1020px 본문 폭, 섹션당 80–112px 수직 리듬.
- **색**: 무채색 위 단일 강조색(`#0071e3`). 상태 색은 의미가 있을 때만 씁니다.
- **모션**: `cubic-bezier(0.28, 0.11, 0.32, 1)`로 통일하고, 스크롤 진입 시
  한 번만 페이드-업 합니다(`prefers-reduced-motion` 존중).
- **크롬**: 반투명 + `backdrop-filter` 상단 바, 1px 헤어라인 경계.
- 라이트/다크 모두 지원하며, 첫 페인트 전에 인라인 스크립트가 테마를 적용해
  깜빡임이 없습니다.

## 실행

```bash
cp .env.example .env.local     # NEXT_PUBLIC_API_BASE_URL 확인
npm install
npm run dev                    # http://localhost:3000
```

백엔드는 별도로 띄워야 합니다(`compose.yaml` 참고). 브라우저가 백엔드를 직접
호출하므로 백엔드의 `BLOGOPS_CORS_ORIGINS`에 프론트엔드 오리진을 추가하세요.

```bash
npm run build      # 프로덕션 빌드
npm run start      # 빌드 결과 실행
npm run lint       # ESLint
npm run typecheck  # 라우트 타입 생성 + tsc --noEmit
```

## API 타입 재생성

`openapi/blogops.json`은 백엔드 FastAPI 앱에서 추출한 OpenAPI 스냅샷입니다.
백엔드 계약이 바뀌면 스냅샷을 갱신한 뒤 타입을 다시 생성합니다.

```bash
# 백엔드 저장소에서 (BLOGOPS_DOCS_ENABLED=true로 앱을 띄운 뒤)
curl -s http://localhost:8000/openapi.json > frontend/openapi/blogops.json

cd frontend && npm run gen:api
```

`src/lib/api/schema.d.ts`는 생성 파일이므로 직접 수정하지 않습니다.

## 구조

```
src/
  app/
    (marketing)/        홈, 제품, 워크플로, 보안, 요금제, 상태 페이지
    (auth)/             로그인, 가입, MFA, 비밀번호 재설정, 이메일 인증
    console/            운영 콘솔 (인증 필요)
  components/
    ui/                 디자인 시스템 프리미티브
    marketing/          랜딩 전용 섹션과 시각 요소
    auth/               인증 폼
    console/            콘솔 화면과 공용 조각
  lib/
    api/                schema(생성) · client · endpoints · tokens · errors
    auth/               세션 컨텍스트
    hooks/              SWR 래퍼, 마운트 감지
    labels.ts           백엔드 enum → 한국어 라벨·톤
    enums.ts            select 옵션 목록
    format.ts           날짜·숫자·통화 포맷 (UTC → 워크스페이스 시간대)
```

## 백엔드 계약에서 지킨 것

프론트엔드는 백엔드가 강제하는 규칙을 UI에서도 그대로 드러냅니다.

- **승인 고정값**: 승인·발행 요청은 콘텐츠 버전 ID, 콘텐츠 해시, 잠금 버전을
  함께 보냅니다. 값이 어긋나면 백엔드가 거부하므로, 오래된 탭이 최신 콘텐츠를
  승인하는 일이 생기지 않습니다.
- **낙관적 잠금**: 모든 쓰기에 `expected_lock_version`을 실어 보내고, 충돌
  응답을 받으면 목록을 다시 읽어 최신 값을 확보합니다.
- **멱등성**: 재실행 가능한 쓰기(생성 작업, 발행, 대량 작업, 결제)는
  `Idempotency-Key` 헤더를 붙입니다.
- **네이버**: 공식 자동 게시 API가 없으므로 발행 UI에서 무인 게시를 제공하지
  않고, 수동 패키지 생성 경로만 노출합니다.
- **토큰 회전**: Refresh Token은 1회용이라, 동시에 발생한 401이 같은 토큰을
  두 번 쓰지 않도록 갱신 요청을 하나로 합칩니다(`src/lib/api/client.ts`).
- **에러**: 백엔드의 표준 에러 봉투(`code`/`message`/`request_id`/`fields`)를
  그대로 해석해, 폼에는 필드별 사유를, 오류 패널에는 `request_id`를 보여 줍니다.

## 인증

액세스·리프레시 토큰은 `localStorage`의 단일 키(`blogops.session`)에 저장하고
`useSyncExternalStore`로 읽습니다. 덕분에 첫 클라이언트 렌더부터 값이 정확하고,
다른 탭의 로그인·로그아웃이 즉시 반영됩니다. 워크스페이스 전환은 토큰 스코프가
바뀌는 일이므로 리프레시 토큰을 새 워크스페이스로 교환합니다.

## 접근성

- 모든 인터랙티브 요소에 보이는 포커스 링(`:focus-visible`).
- 모달은 포커스를 가두고 Esc로 닫히며, 닫을 때 이전 포커스를 복원합니다.
- 브리프 보드의 드래그 앤 드롭에는 키보드로 쓸 수 있는 select 대체 경로가
  있습니다.
- 넓은 표는 자체 컨테이너 안에서만 가로 스크롤되어 본문이 밀리지 않습니다.

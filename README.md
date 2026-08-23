# BlogOps AI Backend

BlogOps AI는 근거 기반 콘텐츠 생성, 품질 검수, 승인, 공식 채널 발행과 성과 분석을
통합하는 멀티테넌트 SaaS의 백엔드입니다. 이 저장소에는 프론트엔드나 브라우저 확장
코드가 포함되지 않습니다.

현재 `1-backend-foundation` 단계는 다음 공통 기반을 제공합니다.

- FastAPI 애플리케이션과 버전이 있는 `/v1` API 경계
- PostgreSQL/pgvector, Redis, Celery 기반 실행 환경
- UTC 저장, 구조화 로그, Request ID, 표준 오류 응답과 기본 보안 헤더
- 워크스페이스 컨텍스트와 권한 검사 인터페이스
- PostgreSQL RLS, 멱등성 레코드, Transactional Outbox, 불변 감사 로그
- 명시적인 장기 작업 상태 전이 규칙
- liveness/readiness와 Prometheus/OpenTelemetry 관측성 진입점

전체 백엔드 범위와 단계는 `ai_blog_automation_service_plan.md`와
`ai_blog_automation_function_spec.md`를 정본으로 삼습니다.

## API 상태 확인

- `GET /health/live`: 프로세스 liveness
- `GET /health/ready`: PostgreSQL과 Redis readiness
- `GET /metrics`: Prometheus 형식 메트릭(설정으로 비활성화 가능)

환경 변수와 원격 실행 방법은 `.env.example` 및 `deploy/` 문서를 참고하십시오.

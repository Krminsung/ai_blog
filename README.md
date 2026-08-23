# BlogOps AI Backend

BlogOps AI는 근거 기반 콘텐츠 생성, 품질 검수, 승인, 공식 채널 발행과 성과 분석을
통합하는 멀티테넌트 SaaS의 백엔드입니다. 이 저장소에는 프론트엔드나 브라우저 확장
코드가 포함되지 않습니다.

현재 `7-publishing-analytics` 단계까지 다음 백엔드 기반을 제공합니다.

- FastAPI 애플리케이션과 버전이 있는 `/v1` API 경계
- PostgreSQL/pgvector, Redis, Celery 기반 실행 환경
- UTC 저장, 구조화 로그, Request ID, 표준 오류 응답과 기본 보안 헤더
- 워크스페이스 컨텍스트와 권한 검사 인터페이스
- PostgreSQL RLS, 멱등성 레코드, Transactional Outbox, 불변 감사 로그
- 명시적인 장기 작업 상태 전이 규칙
- liveness/readiness와 Prometheus/OpenTelemetry 관측성 진입점
- Argon2id 인증, 회전 Refresh Token, TOTP MFA, 세션·기기 관리
- Workspace/Membership/Role, 대행사 계층과 PostgreSQL 테넌트 격리
- 불변 브랜드·상품 스냅샷, 페르소나, 가격·권리·제휴 고지
- 파일·URL 지식 수집, 악성코드 검사, 파싱·PII 마스킹·청킹·임베딩
- MinIO 원본 버전과 위치 계보, 권한 기반 하이브리드 지식 검색
- 공식·계약·사용자 제공 출처만 허용하는 키워드 수집, 캐시·호출 한도·계보
- 키워드 정규화·의도·추세·점수·군집과 1,000행 중복 수용 시나리오
- 캠페인·예산·토픽·아이디어와 불변 콘텐츠 브리프 버전·단계 승인
- 워크스페이스 시간대 기반 콘텐츠 캘린더와 승인 후 저장되는 월간 계획안
- 워크스페이스·요청 주체 범위의 공용 멱등성과 통합 Job 조회·제어 API
- 50개 콘텐츠 유형별 입력·안전 계약과 승인된 브리프 기반 생성 경계
- 브랜드·상품·페르소나·자료·키워드·템플릿·모델·가격의 불변 입력 스냅샷
- 단계형 생성 Job, 부분 결과, 모델 실행 계보·원가 메타와 버전형 콘텐츠 보관함
- 공식 검색 실행 경계, 연구 자료 등급, 주장·인용 연결과 최신성·권리 정책
- 미구성 모델·검색·예산 공급자를 성공 처리하지 않는 fail-closed 워커 경계
- 형태소·자연스러움·SEO·중복·팩트/인용·안전 정책의 버전 고정 품질 증거
- 설명 가능한 7요소 품질 산식, 계층형 정책 우선순위와 예외 불가 Hard Block
- 콘텐츠 버전·해시를 DB 외래키까지 고정하는 다단계·정족수 승인과 승인 증명
- 새 버전·복원·재생성·승인 영향 메타데이터 변경 시 기존 승인 자동 무효화
- 격리 업로드·악성코드/EXIF/PII 검사·불변 버전·권리 판정 기반 미디어 자산 관리
- 공급자 정책·회로 차단·할당량·비용 Hold를 고정하는 이미지 생성·편집 작업
- 콘텐츠 버전과 채널·지역·사용 목적의 권리를 함께 고정하는 이미지 계획·사용 계보
- 서버 검증 CSV/XLSX Snapshot, PII 마스킹, 행 멱등성·재시도·승인 기반 대량 생성
- 행별 비용 Hold·Spam/가치 Gate·Kill Switch와 서명 Callback·Export·Schedule 경계
- WordPress·Ghost·Blogger 공식 API와 승인된 고객 CMS의 멱등 Saga 발행·동기화
- DST 안전 예약, 원격 충돌 감지·복구, 미디어 권리·승인 버전 고정 및 발행 계보
- 네이버 무인 게시를 금지하는 불변 수동 패키지·체크리스트·사용자 확인 흐름
- 공식 분석 공급자·원본 증거·지표 정의를 고정하는 성과·전환·ROI·리포트 파이프라인
- 근거·정책·모델·비용 Snapshot과 승인 Gate를 고정하는 14종 콘텐츠 재활용

전체 백엔드 범위와 단계는 `ai_blog_automation_service_plan.md`와
`ai_blog_automation_function_spec.md`를 정본으로 삼습니다.

## API 상태 확인

- `GET /health/live`: 프로세스 liveness
- `GET /health/ready`: PostgreSQL과 Redis readiness
- `GET /metrics`: Prometheus 형식 메트릭(설정으로 비활성화 가능)

환경 변수와 원격 실행 방법은 `.env.example` 및 `deploy/` 문서를 참고하십시오.

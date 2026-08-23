# ADR 0001: 백엔드 기반 아키텍처

- 상태: 승인
- 적용 단계: `1-backend-foundation`
- 기준일: 2026-08-23

## 결정

백엔드는 Python 3.12와 FastAPI 기반의 모듈형 모놀리스로 시작하며, API와 Worker 프로세스를
같은 패키지에서 분리 실행한다. PostgreSQL 16과 pgvector를 정본 데이터 저장소로 사용하고,
Redis는 캐시와 Celery 전송 계층으로 사용한다. 파일·이미지 저장소 경계는 S3 호환 API로
고정한다.

초기부터 다음 불변식을 적용한다.

1. 모든 업무 시각은 UTC로 저장한다.
2. 인증된 서버 상태로만 `Principal`과 workspace context를 만들며 클라이언트의 workspace
   헤더를 권한 근거로 신뢰하지 않는다.
3. 테넌트 테이블은 명시적 `workspace_id`, PostgreSQL RLS, 애플리케이션 RBAC를 함께 쓴다.
4. 외부 부수효과는 Transactional Outbox와 멱등성 레코드를 거친다.
5. 승인·발행·원장은 변경 대신 새 버전 또는 역분개를 남긴다.
6. Request ID가 로그·응답·감사·비동기 이벤트로 이어지게 한다.
7. 비밀 값이나 원문 개인정보는 로그에 기록하지 않는다.

## 워크플로 선택

기획서는 Celery/Dramatiq 또는 Temporal을 허용한다. 초기 구현은 운영 복잡도를 제한하기 위해
Redis + Celery와 데이터베이스 상태 머신을 사용한다. 장기 실행 흐름은 업무 Job/Step 테이블을
정본으로 삼고 Outbox·보상 작업·Reconciliation으로 복구한다. 이 구조로 충족하기 어려운 장기
타이머나 다중 보상 흐름이 확인되면 Temporal 어댑터로 교체할 수 있도록 도메인 코드에서 Celery를
직접 호출하지 않는다.

## 배포 경계

운영 `compose.yaml`은 호스트 포트를 열지 않는다. API만 `blogops-ingress` 전용 external
네트워크에 연결하고, PostgreSQL·Redis·Object Storage는 `blogops-data` internal 네트워크에
격리한다. 공용 80/443은 `/root/infra/reverse-proxy`의 단일 Nginx가 소유하며 Docker DNS의
`api:8000`으로 전달해야 한다. `deploy/compose.test.yaml`의 loopback 포트는 지정 검증 서버의
일회성 테스트에서만 사용하고 검증 종료 후 해당 스택과 볼륨을 제거한다.

## 보안 역할

마이그레이션 소유자(`blogops_owner`)와 런타임 API/Worker DB 역할(`blogops_app`,
`blogops_worker`)을 분리한다. 두 런타임 역할은 RLS를 우회할 수 없고 업무 트랜잭션마다 검증된
workspace를 `app.current_workspace_id`에 설정한다. Cross-tenant 예약 탐색은 일반 테이블 우회
권한이 아니라 후속 단계의 제한된 `SECURITY DEFINER` 큐 함수로 제공하며, 런타임 자격 증명에
BYPASSRLS를 부여하지 않는다.

## 후속 단계

- 2단계: 사용자·세션·워크스페이스·역할과 모든 업무 테이블의 RLS를 구체화한다.
- 4단계: 모델 공급자 어댑터 및 생성 상태 머신을 Celery 업무와 연결한다.
- 7단계: CMS 부수효과를 Outbox/Saga/Reconciliation으로 연결한다.
- 9단계: 운영 DB 역할, 백업/PITR, 단일 인그레스/TLS와 재해복구 절차를 최종 검증한다.
